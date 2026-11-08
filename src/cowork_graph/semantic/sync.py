"""Stat-scan sync: re-extract and re-embed only what changed (plan.md Phase 8, decision 5).

Keys every file by (size, mtime_ns), confirming with md5 before doing work,
so gitignored PDFs and images that `git diff` never sees are covered, and a
fresh checkout that rewrites mtimes costs one hash per file instead of a
re-embed. A failed file records its error and md5 and is retried only when it
changes (or with retry_errors) — the old RAG re-OCR'd the same junk scans
every night.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cowork_graph.config import Config
from cowork_graph.semantic import chunking, extract, store
from cowork_graph.semantic.discover import FoundFile, discover
from cowork_graph.semantic.models import EMBED_MODEL, Models
from cowork_graph.semantic.pii import Detector

COMMIT_EVERY = 25


@dataclass
class SyncSummary:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    touched: int = 0
    deleted: int = 0
    empty: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    chunks: int = 0
    seconds: float = 0.0

    def line(self) -> str:
        return (
            f"semantic: {self.scanned} files scanned, +{self.added} added, ~{self.updated} updated, "
            f"={self.touched} touched, -{self.deleted} deleted, {self.empty} empty, "
            f"{self.skipped} skipped, {len(self.errors)} errors, {self.chunks} chunks embedded, "
            f"{self.seconds:.1f}s"
        )


@dataclass
class _Prepared:
    found: FoundFile
    md5: str
    unchanged: bool = False
    extracted: extract.Extracted | None = None
    chunks: list[store.Chunk] = field(default_factory=list)
    error: str | None = None
    status: str = "ok"


def _md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_dates(root: Path) -> dict[str, int]:
    """Last commit time per tracked path, in one `git log` pass.

    A fresh clone stamps every file with today's mtime, so mtime says nothing
    about when a doc was last worked on; the commit history does. Untracked
    files (most PDFs and images) fall back to mtime.
    """
    try:
        out = subprocess.run(  # fixed argv, no shell
            ["git", "-C", str(root), "log", "--format=%x00%ct", "--name-only", "--no-renames"],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    dates: dict[str, int] = {}
    stamp = 0
    for line in out.splitlines():
        if line.startswith("\0"):
            stamp = int(line[1:])
        elif line and line not in dates:
            dates[line] = stamp  # log is newest-first: first sighting wins
    return dates


def _title(rel: str) -> str:
    return Path(rel).stem.replace("-", " ").replace("_", " ")


def _prepare(found: FoundFile, prior_md5: str | None, prior_ok: bool, ocr: bool) -> _Prepared:
    md5 = _md5(found.abs_path)
    prep = _Prepared(found=found, md5=md5)
    if prior_md5 == md5 and prior_ok:
        prep.unchanged = True
        return prep
    try:
        prep.extracted = extract.extract(found.abs_path, found.kind, ocr=ocr)
    except Exception as exc:  # noqa: BLE001 — recorded per file, never silently dropped
        prep.status, prep.error = "error", f"{type(exc).__name__}: {exc}"[:500]
        return prep
    prep.chunks = chunking.chunk_sections(prep.extracted.sections)
    if not prep.chunks:
        prep.status = "empty"
    elif len(prep.chunks) > chunking.MAX_FILE_CHUNKS:
        prep.status = "skipped"
        prep.error = f"{len(prep.chunks)} chunks exceeds MAX_FILE_CHUNKS={chunking.MAX_FILE_CHUNKS}"
        prep.chunks = []
    return prep


def sync(
    cfg: Config,
    *,
    models: Models,
    detector: Detector | None = None,
    only: Iterable[str] | None = None,
    retry_errors: bool = False,
    wait_for_lock: bool = False,
    log: Callable[[str], None] = print,
) -> SyncSummary:
    """Bring semantic.db in line with the corpus. `only` limits the run to
    specific relative paths (save_memory) and disables deletion."""
    t0 = time.monotonic()
    summary = SyncSummary()
    sem = cfg.semantic
    root = cfg.cowork_root.resolve()
    with store.writer_lock(sem.db_path, wait=wait_for_lock):
        conn = store.connect(sem.db_path)
        try:
            store.check_models(conn, EMBED_MODEL)
            found = discover(root)
            if only is not None:
                wanted = set(only)
                found = {k: v for k, v in found.items() if k in wanted}
            summary.scanned = len(found)
            index = store.files_index(conn)
            pii_on = detector is not None and detector.available()
            dates = git_dates(root)

            if only is None:
                for rel in sorted(set(index) - set(found)):
                    store.delete_file(conn, rel)
                    summary.deleted += 1

            todo: list[tuple[FoundFile, str | None, bool]] = []
            for rel, f in found.items():
                row = index.get(rel)
                needs_pii = (
                    pii_on and row is not None and row["status"] == "ok" and not row["pii_scanned"]
                )
                same_stat = (
                    row is not None and row["size"] == f.size and row["mtime_ns"] == f.mtime_ns
                )
                retry = retry_errors and row is not None and row["status"] == "error"
                if same_stat and not needs_pii and not retry:
                    continue
                prior_ok = row is not None and row["status"] != "error" and not needs_pii
                todo.append((f, row["md5"] if row is not None else None, prior_ok))

            if todo:
                log(f"semantic: {len(todo)} of {len(found)} files to check")
            done = 0
            with ThreadPoolExecutor(max_workers=sem.workers) as pool:
                futures = [pool.submit(_prepare, f, md5, ok, sem.ocr) for f, md5, ok in todo]
                for fut in as_completed(futures):
                    prep = fut.result()
                    f = prep.found
                    modified = dates.get(f.rel) or f.mtime_ns // 1_000_000_000
                    _write(
                        conn, prep, index, models, detector if pii_on else None, summary, modified
                    )
                    done += 1
                    if done % COMMIT_EVERY == 0:
                        conn.commit()
                        log(f"semantic: {done}/{len(todo)} files, {summary.chunks} chunks")
            store.set_meta(conn, "last_sync", datetime.now(UTC).isoformat(timespec="seconds"))
            conn.commit()
        finally:
            conn.close()
    summary.seconds = time.monotonic() - t0
    return summary


def _write(
    conn, prep: _Prepared, index, models: Models, detector, summary: SyncSummary, modified: int
) -> None:
    f = prep.found
    if prep.unchanged:
        store.touch_file(conn, f.rel, f.size, f.mtime_ns, modified)
        summary.touched += 1
        return
    vectors: list[list[float]] = []
    pii = None
    status, error = prep.status, prep.error
    if status == "ok":
        try:
            title = _title(f.rel)
            vectors = models.embed_documents([chunking.embed_text(c, title) for c in prep.chunks])
            if detector is not None:
                pii = detector.detect_many([c.text for c in prep.chunks])
        except Exception as exc:  # noqa: BLE001 — a model failure fails this file loudly
            status, error, vectors, pii = "error", f"{type(exc).__name__}: {exc}"[:500], [], None
    chunks = prep.chunks if status == "ok" else []
    text = prep.extracted.text if (prep.extracted and status == "ok") else ""
    store.replace_file(
        conn,
        path=f.rel,
        kind=f.kind,
        size=f.size,
        mtime_ns=f.mtime_ns,
        modified=modified,
        md5=prep.md5,
        status=status,
        error=error,
        text=text,
        chunks=chunks,
        vectors=vectors,
        pii=pii,
        ocr_pages=prep.extracted.ocr_pages if prep.extracted else 0,
    )
    if status == "error":
        summary.errors.append((f.rel, error or ""))
    elif status == "empty":
        summary.empty += 1
    elif status == "skipped":
        summary.skipped += 1
    elif f.rel in index:
        summary.updated += 1
    else:
        summary.added += 1
    summary.chunks += len(chunks)
