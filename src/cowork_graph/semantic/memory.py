"""Saved memories as markdown in the corpus (plan.md Phase 8, decision 6).

A memory is a small frontmatter-tagged doc under `memory/saved/` (config
`[semantic] memory_dir`). Markdown stays canonical: memories sync between
machines with git, survive any index rebuild, and show up in the graph and
Obsidian like every other doc. They are indexed immediately on save so
recall works before the next commit.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from cowork_graph.config import Config
from cowork_graph.semantic import pii, search, store, sync
from cowork_graph.semantic.models import Models
from cowork_graph.semantic.pii import Detector

MAX_MEMORY_CHARS = 8000
TITLE_MAX = 80
_TAG = re.compile(r"^[a-z0-9-]+/[a-z0-9._-]+$")


class MemoryRefused(ValueError):
    pass


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:8]
    return "-".join(words)[:60].strip("-") or "memory"


def _title_from(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0].rstrip(" .:")
    if len(first) <= TITLE_MAX:
        return first
    # Cut on a word boundary so titles never end mid-word.
    return first[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:-(") + "…"


def guard(text: str, cfg: Config, detector: Detector | None) -> None:
    """Refuse notes holding raw identifiers (cowork-rag-chat PII_MEMORY_GUARD)."""
    hits = pii.obvious_identifiers(text)
    if detector is not None and detector.available():
        found = detector.detect_many([text])
        hits += [
            v
            for label, vals in found.items()
            if pii.maskable(label, cfg.semantic.pii_mask_labels)
            for v in vals
            if pii.mask_worthy(v)
        ]
    if hits:
        tails = ", ".join(sorted({f"…{pii._alnum(h)[-4:]}" for h in hits}))
        raise MemoryRefused(
            f"memory refused: it contains raw identifiers ({tails}). "
            "Save it without the number, or with only the last four digits."
        )


def write_memory(
    cfg: Config,
    text: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    saved_at: datetime | None = None,
    footer: str | None = None,
) -> Path:
    text = (text or "").strip()
    if not text:
        raise MemoryRefused("memory is empty")
    if len(text) > MAX_MEMORY_CHARS:
        raise MemoryRefused(f"memory is {len(text)} chars; limit {MAX_MEMORY_CHARS}")
    extra = []
    for t in tags or []:
        t = t.strip().lower()
        if not _TAG.match(t):
            raise MemoryRefused(f"tag {t!r} must look like axis/value, e.g. project/kalshicaster")
        if t.startswith(("status/", "type/")):
            continue  # fixed below: memories are always type/memory, status/reference
        extra.append(t)
    saved_at = saved_at or datetime.now(UTC)
    title = (title or _title_from(text)).strip()
    folder = cfg.cowork_root / cfg.semantic.memory_dir
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{saved_at.date().isoformat()}-{_slug(title)}"
    path = folder / f"{stem}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.md"
        n += 1
    tag_lines = "".join(f"  - {t}\n" for t in ["status/reference", "type/memory", *extra])
    stamp = saved_at.strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"---\ntags:\n{tag_lines}saved: {saved_at.isoformat(timespec='seconds')}\n---\n\n"
        f"# {title}\n\n{text}\n\n_{footer or f'Saved via save_memory on {stamp}.'}_\n"
    )
    # Exclusive create: two saves racing on one slug can't overwrite each other.
    with path.open("x", encoding="utf-8") as f:
        f.write(body)
    return path


def save_memory(
    cfg: Config,
    models: Models,
    detector: Detector | None,
    text: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    guard(text, cfg, detector)
    path = write_memory(cfg, text, title=title, tags=tags)
    rel = path.relative_to(cfg.cowork_root)
    resp = {
        "path": rel.as_posix(),
        "indexed": False,
        "note": "saved as an uncommitted file; commit cowork to sync it to the other machine",
    }
    try:
        sync.sync(cfg, models=models, detector=detector, only=[rel.as_posix()], log=lambda _m: None)
        resp["indexed"] = True
    except store.SemanticBusy:
        resp["note"] += "; a sync is running, so recall will see it after that finishes"
    return resp


def recall(cfg: Config, models: Models, query: str, limit: int = 3) -> dict:
    res = search.search(
        cfg, models, query, path_prefix=cfg.semantic.memory_dir, limit=limit, per_source=1
    )
    return res.as_dict()


def import_rag_memories(cfg: Config, json_path: Path, detector: Detector | None) -> list[str]:
    """One-time import of cowork-rag-chat memories exported from its Chroma index."""
    rows = json.loads(json_path.read_text("utf-8"))
    written = []
    for row in rows:
        text = (row.get("chroma:document") or row.get("text") or "").strip()
        if not text:
            continue
        created = row.get("created", "")
        try:
            saved_at = datetime.strptime(created, "%Y-%m-%d %H:%M UTC").replace(tzinfo=UTC)
        except ValueError:
            saved_at = datetime.now(UTC)
        guard(text, cfg, detector)
        path = write_memory(
            cfg,
            text,
            saved_at=saved_at,
            footer=f"Imported from cowork-rag-chat on {datetime.now(UTC).date()} "
            f"(saved there {created or 'at an unknown time'}).",
        )
        written.append(path.relative_to(cfg.cowork_root).as_posix())
    return written
