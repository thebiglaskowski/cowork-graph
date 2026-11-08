"""Hybrid retrieval joined to the graph (plan.md Phase 8, decision 3).

vector top-K + FTS5 BM25 top-K → filter → reciprocal-rank fusion → MiniLM
rerank of the fused pool → per-source cap → small capped recency bonus. Each
hit then carries the graph's view of its doc: title, effective (hub-derived)
status and projects, so "active AutoScript docs about X" is one call.
"""

from __future__ import annotations

import posixpath
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import sqlite_vec

from cowork_graph.config import Config
from cowork_graph.queries import EFFECTIVE_STATUS
from cowork_graph.semantic import pii, store
from cowork_graph.semantic.models import Models

VECTOR_K = 200
FTS_K = 200
RRF_K = 60
# Rerank is ~8 ms per 600-char chunk on CPU and dominates query time; 24
# keeps warm queries near 0.6 s on the 46k-chunk corpus (2026-11-07).
RERANK_POOL = 24
PER_SOURCE = 3
MAX_LIMIT = 30
# Recency (cowork-rag-chat's RECENCY_* defaults): today's file gets 0.1,
# halving every 60 days, never more than 0.05 — age alone can't beat a
# clearly better match.
RECENCY_WEIGHT = 0.1
RECENCY_HALF_LIFE_DAYS = 60
RECENCY_MAX_BOOST = 0.05
READ_PAGE_CHARS = 40_000
KINDS = ("md", "pdf", "image", "office", "text")

_TOKEN = re.compile(r"\w+", re.UNICODE)


@dataclass
class Hit:
    path: str
    title: str | None
    kind: str
    status: str | None
    projects: list[str]
    heading: str | None
    page: int | None
    snippet: str
    score: float
    modified: str


@dataclass
class SearchResponse:
    query: str
    items: list[Hit]
    candidates: int
    timings_ms: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        if not self.warnings:
            d.pop("warnings")
        return d


def fts_query(text: str) -> str | None:
    tokens = [t for t in _TOKEN.findall(text.lower()) if len(t) > 1]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens))


def _graph_conn(cfg: Config) -> sqlite3.Connection | None:
    if not cfg.db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def graph_info(cfg: Config, paths: list[str]) -> dict[str, dict]:
    """{path: {title, status, projects}} for paths the graph knows (markdown docs)."""
    info: dict[str, dict] = {}
    conn = _graph_conn(cfg)
    if conn is None or not paths:
        return info
    try:
        for i in range(0, len(paths), 500):
            batch = paths[i : i + 500]
            marks = ",".join("?" * len(batch))
            for r in conn.execute(
                f"SELECT d.path, d.title, {EFFECTIVE_STATUS} AS status FROM doc d"
                f" WHERE d.path IN ({marks})",
                batch,
            ):
                info[r["path"]] = {"title": r["title"], "status": r["status"], "projects": []}
            for r in conn.execute(
                "SELECT source_id, target_id FROM edge WHERE source_type='doc'"
                f" AND edge_type='MEMBER_OF_PROJECT' AND source_id IN ({marks})",
                batch,
            ):
                if r["source_id"] in info:
                    info[r["source_id"]]["projects"].append(r["target_id"])
    finally:
        conn.close()
    return info


def _since_epoch(since: str) -> int:
    try:
        dt = datetime.fromisoformat(since)
    except ValueError as exc:
        raise ValueError(f"since must be an ISO date like 2026-10-01; got {since!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _prefix(p: str | None) -> str | None:
    if not p:
        return None
    return p.strip().strip("/") + "/"


def pii_values(conn: sqlite3.Connection, paths: list[str], cfg: Config) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {p: [] for p in paths}
    if not paths:
        return out
    marks = ",".join("?" * len(paths))
    rows = conn.execute(f"SELECT path, label, value FROM pii WHERE path IN ({marks})", paths)
    for r in rows:
        if pii.maskable(r["label"], cfg.semantic.pii_mask_labels) and pii.mask_worthy(r["value"]):
            out[r["path"]].append(r["value"])
    return out


def search(
    cfg: Config,
    models: Models,
    query: str,
    *,
    scope: str | None = None,
    project: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    path_prefix: str | None = None,
    since: str | None = None,
    limit: int = 8,
    per_source: int = PER_SOURCE,
    use_recency: bool = True,
) -> SearchResponse:
    if not query or not query.strip():
        raise ValueError("query is empty")
    if kind and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}; got {kind!r}")
    limit = max(1, min(int(limit), MAX_LIMIT))
    timings: dict[str, float] = {}
    warnings: list[str] = []
    t = time.monotonic()

    def lap(name: str) -> None:
        nonlocal t
        now = time.monotonic()
        timings[name] = round((now - t) * 1000, 1)
        t = now

    qvec = models.embed_query(query)
    lap("embed")
    conn = store.connect(cfg.semantic.db_path, readonly=True)
    try:
        vec_ids = [
            r["rowid"]
            for r in conn.execute(
                "SELECT rowid, distance FROM chunk_vec WHERE embedding MATCH ? AND k = ?"
                " ORDER BY distance",
                (sqlite_vec.serialize_float32(qvec), VECTOR_K),
            )
        ]
        lap("vector")
        fts_ids: list[int] = []
        fq = fts_query(query)
        if fq:
            fts_ids = [
                r["rowid"]
                for r in conn.execute(
                    "SELECT rowid FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY bm25(chunk_fts)"
                    " LIMIT ?",
                    (fq, FTS_K),
                )
            ]
        lap("keyword")

        ids = list(dict.fromkeys(vec_ids + fts_ids))
        rows: dict[int, sqlite3.Row] = {}
        for i in range(0, len(ids), 500):
            batch = ids[i : i + 500]
            marks = ",".join("?" * len(batch))
            for r in conn.execute(
                "SELECT c.id, c.path, c.heading, c.page, c.text, f.kind, f.modified"
                f" FROM chunk c JOIN file f ON f.path = c.path WHERE c.id IN ({marks})",
                batch,
            ):
                rows[r["id"]] = r

        # ---- filters -------------------------------------------------------
        # A leading "-" excludes a scope (cowork-rag-chat syntax): "-personal"
        # searches everything outside personal/.
        exclude = _prefix(scope[1:]) if scope and scope.startswith("-") else None
        include = None if exclude else _prefix(scope)
        prefixes = [p for p in (include, _prefix(path_prefix)) if p]
        since_ts = _since_epoch(since) if since else None
        paths = sorted({r["path"] for r in rows.values()})
        info = graph_info(cfg, paths)
        if (status or project) and not cfg.db_path.exists():
            raise RuntimeError("status/project filters need the graph DB; run: cowork-graph build")

        def keep(r: sqlite3.Row) -> bool:
            if any(not r["path"].startswith(p) for p in prefixes):
                return False
            if exclude and r["path"].startswith(exclude):
                return False
            if kind and r["kind"] != kind:
                return False
            if since_ts is not None and r["modified"] < since_ts:
                return False
            meta = info.get(r["path"])
            if status and (meta is None or meta["status"] != status):
                return False
            return not (project and (meta is None or project not in meta["projects"]))

        allowed = {cid for cid, r in rows.items() if keep(r)}
        lap("filter")

        # ---- reciprocal-rank fusion ---------------------------------------
        fused: dict[int, float] = {}
        for ranked in (vec_ids, fts_ids):
            rank = 0
            for cid in ranked:
                if cid not in allowed:
                    continue
                rank += 1
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        pool = sorted(fused, key=fused.get, reverse=True)[:RERANK_POOL]
        lap("fuse")

        # ---- rerank --------------------------------------------------------
        def rerank_text(r: sqlite3.Row) -> str:
            title = (info.get(r["path"]) or {}).get("title") or posixpath.basename(r["path"])
            context = " > ".join(x for x in (title, r["heading"]) if x)
            return f"{context}\n{r['text']}"

        scores = models.rerank(query, [rerank_text(rows[c]) for c in pool])
        lap("rerank")
        now = time.time()
        scored = []
        for cid, score in zip(pool, scores):
            if use_recency:
                age_days = max(0.0, (now - rows[cid]["modified"]) / 86400)
                bonus = RECENCY_WEIGHT * 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
                score += min(RECENCY_MAX_BOOST, bonus)
            scored.append((cid, score))
        scored.sort(key=lambda x: x[1], reverse=True)

        picked: list[tuple[int, float]] = []
        per: dict[str, int] = {}
        for cid, score in scored:
            path = rows[cid]["path"]
            if per.get(path, 0) >= per_source:
                continue
            per[path] = per.get(path, 0) + 1
            picked.append((cid, score))
            if len(picked) >= limit:
                break

        masks = pii_values(conn, sorted({rows[c]["path"] for c, _ in picked}), cfg)
    finally:
        conn.close()

    items = []
    for cid, score in picked:
        r = rows[cid]
        meta = info.get(r["path"]) or {}
        values = masks.get(r["path"], [])
        items.append(
            Hit(
                path=r["path"],
                title=meta.get("title") or posixpath.basename(r["path"]),
                kind=r["kind"],
                status=meta.get("status"),
                projects=sorted(meta.get("projects", [])),
                heading=pii.mask(r["heading"], values) if r["heading"] else None,
                page=r["page"],
                snippet=pii.mask(r["text"], values),
                score=round(min(score, 1.0), 4),
                modified=datetime.fromtimestamp(r["modified"], UTC).date().isoformat(),
            )
        )
    lap("hydrate")
    if not items and (scope or project or status or kind or path_prefix or since):
        warnings.append("no results inside the filters; try loosening them")
    return SearchResponse(query, items, len(fused), timings, warnings)


def normalize_path(path: str) -> str:
    """Sandbox: relative, inside the corpus, no traversal."""
    p = posixpath.normpath(path.replace("\\", "/").strip())
    if p.startswith(("/", "../")) or p in ("..", "."):
        raise ValueError(f"path must be relative to the cowork root: {path!r}")
    return p


def read_document(cfg: Config, path: str, page: int = 1) -> dict:
    """Windowed read of an indexed file's extracted text (incl. OCR), masked.

    Only files in the semantic index are readable — that is the sandbox:
    anything outside the corpus, or skipped by discovery, does not exist here.
    """
    rel = normalize_path(path)
    conn = store.connect(cfg.semantic.db_path, readonly=True)
    try:
        f = conn.execute("SELECT * FROM file WHERE path=?", (rel,)).fetchone()
        if f is None:
            raise FileNotFoundError(f"not in the semantic index: {rel}")
        if f["status"] != "ok":
            return {
                "path": rel,
                "kind": f["kind"],
                "status": f["status"],
                "error": f["error"],
                "text": "",
                "page": 1,
                "pages": 0,
            }
        row = conn.execute("SELECT text FROM file_text WHERE path=?", (rel,)).fetchone()
        text = row["text"] if row else ""
        values = pii_values(conn, [rel], cfg)[rel]
    finally:
        conn.close()
    pages = max(1, -(-len(text) // READ_PAGE_CHARS))
    page = max(1, min(int(page), pages))
    window = text[(page - 1) * READ_PAGE_CHARS : page * READ_PAGE_CHARS]
    resp = {
        "path": rel,
        "kind": f["kind"],
        "status": "ok",
        "page": page,
        "pages": pages,
        "text": pii.mask(window, values),
    }
    if page < pages:
        resp["note"] = f"page {page} of {pages}; call again with page={page + 1}"
    return resp
