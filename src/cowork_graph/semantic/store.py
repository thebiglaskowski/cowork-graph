"""semantic.db access: schema bootstrap, sqlite-vec, and per-file replace/delete.

Chroma is gone on purpose (plan.md Phase 8, decision 2): vectors live in a
sqlite-vec `vec0` table keyed by chunk id, so the whole layer is one file with
one writer and WAL readers — no client/server mode to get wrong.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import sqlite_vec

SCHEMA_VERSION = "1"
EMBED_DIM = 768


class SemanticBusy(RuntimeError):
    """Another process holds the semantic writer lock."""


@dataclass
class Chunk:
    ordinal: int
    text: str
    heading: str | None = None
    page: int | None = None


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open semantic.db with sqlite-vec loaded; bootstrap the schema on write opens."""
    if readonly:
        if not path.exists():
            raise FileNotFoundError(
                f"semantic index not built yet: {path} (run: cowork-graph semantic build)"
            )
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        _bootstrap(conn)
    return conn


def _bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript((files("cowork_graph.semantic") / "schema.sql").read_text("utf-8"))
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0("
        f"embedding float[{EMBED_DIM}] distance_metric=cosine)"
    )
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,)
        )
    elif row["value"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"semantic.db schema {row['value']} != code schema {SCHEMA_VERSION}; "
            "delete the file and run: cowork-graph semantic build"
        )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def check_models(conn: sqlite3.Connection, embed_model: str) -> None:
    """Refuse to mix embedding spaces: a model change needs a deliberate rebuild."""
    stored = get_meta(conn, "embed_model")
    if stored is None:
        set_meta(conn, "embed_model", embed_model)
        conn.commit()
    elif stored != embed_model:
        raise RuntimeError(
            f"semantic.db was embedded with {stored}, code uses {embed_model}; "
            "delete the file and run: cowork-graph semantic build"
        )


def files_index(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["path"]: r for r in conn.execute("SELECT * FROM file")}


def _delete_chunks(conn: sqlite3.Connection, path: str) -> None:
    rows = conn.execute("SELECT id, heading, text FROM chunk WHERE path=?", (path,)).fetchall()
    for r in rows:
        # External-content FTS needs the old values to delete the row.
        conn.execute(
            "INSERT INTO chunk_fts (chunk_fts, rowid, heading, text) VALUES ('delete', ?, ?, ?)",
            (r["id"], r["heading"] or "", r["text"]),
        )
        conn.execute("DELETE FROM chunk_vec WHERE rowid=?", (r["id"],))
    conn.execute("DELETE FROM chunk WHERE path=?", (path,))
    conn.execute("DELETE FROM pii WHERE path=?", (path,))
    conn.execute("DELETE FROM file_text WHERE path=?", (path,))


def delete_file(conn: sqlite3.Connection, path: str) -> None:
    _delete_chunks(conn, path)
    conn.execute("DELETE FROM file WHERE path=?", (path,))


def replace_file(
    conn: sqlite3.Connection,
    *,
    path: str,
    kind: str,
    size: int,
    mtime_ns: int,
    modified: int,
    md5: str,
    status: str,
    error: str | None,
    text: str,
    chunks: Sequence[Chunk],
    vectors: Sequence[Sequence[float]],
    pii: dict[str, list[str]] | None,
    ocr_pages: int = 0,
) -> None:
    """Atomically replace everything stored for one file. Caller commits."""
    if len(chunks) != len(vectors):
        raise ValueError(f"{path}: {len(chunks)} chunks but {len(vectors)} vectors")
    _delete_chunks(conn, path)
    conn.execute(
        "INSERT OR REPLACE INTO file (path, kind, size, mtime_ns, modified, md5, status, error,"
        " chunk_count, ocr_pages, pii_scanned, indexed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            path,
            kind,
            size,
            mtime_ns,
            modified,
            md5,
            status,
            error,
            len(chunks),
            ocr_pages,
            1 if pii is not None else 0,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    if text:
        conn.execute("INSERT INTO file_text (path, text) VALUES (?, ?)", (path, text))
    for chunk, vector in zip(chunks, vectors):
        cur = conn.execute(
            "INSERT INTO chunk (path, ordinal, page, heading, text) VALUES (?, ?, ?, ?, ?)",
            (path, chunk.ordinal, chunk.page, chunk.heading, chunk.text),
        )
        cid = cur.lastrowid
        conn.execute(
            "INSERT INTO chunk_fts (rowid, heading, text) VALUES (?, ?, ?)",
            (cid, chunk.heading or "", chunk.text),
        )
        conn.execute(
            "INSERT INTO chunk_vec (rowid, embedding) VALUES (?, ?)",
            (cid, sqlite_vec.serialize_float32(list(vector))),
        )
    for label, values in (pii or {}).items():
        conn.executemany(
            "INSERT OR IGNORE INTO pii (path, label, value) VALUES (?, ?, ?)",
            [(path, label, v) for v in values],
        )


def touch_file(
    conn: sqlite3.Connection, path: str, size: int, mtime_ns: int, modified: int
) -> None:
    """Content unchanged (md5 confirmed) but stat moved — e.g. a fresh git checkout."""
    conn.execute(
        "UPDATE file SET size=?, mtime_ns=?, modified=? WHERE path=?",
        (size, mtime_ns, modified, path),
    )


@contextmanager
def writer_lock(db_path: Path, *, wait: bool = False) -> Iterator[None]:
    """Single-writer guard. Readers (the MCP server) never take it — WAL covers them.

    Non-waiting by default: a git hook that finds a sync already running just
    exits, and the stat-based sync picks its files up next time.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db_path.with_suffix(db_path.suffix + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise SemanticBusy("another semantic sync holds the writer lock") from None
        yield
    finally:
        os.close(fd)
