"""SQLite connection, schema bootstrap, and bulk-insert helpers."""

from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

import cowork_graph

_ENTITY_SEEDS = ("personal", "autoscriptstudio", "nexus-legacy-holdings")
_OWNS_EDGES = (
    # (owner, owned)
    ("nexus-legacy-holdings", "autoscriptstudio"),
)


def _load_schema() -> str:
    return (files("cowork_graph") / "schema.sql").read_text(encoding="utf-8")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (or create) the graph DB and bootstrap the schema if needed."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _bootstrap(conn)
    return conn


def _bootstrap(conn: sqlite3.Connection) -> None:
    schema = _load_schema()
    conn.executescript(schema)
    # Seed entity nodes (idempotent)
    conn.executemany(
        "INSERT OR IGNORE INTO entity (slug) VALUES (?)",
        [(e,) for e in _ENTITY_SEEDS],
    )
    # Seed ownership edges (idempotent)
    conn.executemany(
        "INSERT OR IGNORE INTO edge"
        " (source_type, source_id, edge_type, target_type, target_id, edge_subtype)"
        " VALUES ('entity', ?, 'OWNS', 'entity', ?, '')",
        _OWNS_EDGES,
    )
    # Seed schema_meta (only on first create)
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta (key, value)"
        " VALUES ('parser_version', ?)",
        (cowork_graph.__version__,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Upsert helpers — each accepts an open connection; caller manages the transaction.
# ---------------------------------------------------------------------------


def upsert_doc(
    conn: sqlite3.Connection,
    *,
    path: str,
    title: str | None,
    status: str | None,
    doc_type: str | None,
    word_count: int,
    link_count: int,
    last_modified: str | None,
    parsed_at: str,
    parse_status: str,
    parse_notes: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO doc"
        " (path, title, status, doc_type, word_count, link_count,"
        "  last_modified, parsed_at, parse_status, parse_notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (path, title, status, doc_type, word_count, link_count,
         last_modified, parsed_at, parse_status, parse_notes),
    )


def upsert_tag(conn: sqlite3.Connection, *, tag: str) -> None:
    conn.execute("INSERT OR IGNORE INTO tag (tag) VALUES (?)", (tag,))


def upsert_person(
    conn: sqlite3.Connection,
    *,
    slug: str,
    display_name: str,
    role: str | None,
    source_doc: str | None,
    is_ghost: bool,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO person (slug, display_name, role, source_doc, is_ghost)"
        " VALUES (?, ?, ?, ?, ?)",
        (slug, display_name, role, source_doc, int(is_ghost)),
    )


def upsert_person_alias(conn: sqlite3.Connection, *, slug: str, alias: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO person_alias (slug, alias) VALUES (?, ?)",
        (slug, alias),
    )


def upsert_project(
    conn: sqlite3.Connection,
    *,
    slug: str,
    hub_doc: str | None,
    is_ghost: bool,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO project (slug, hub_doc, is_ghost) VALUES (?, ?, ?)",
        (slug, hub_doc, int(is_ghost)),
    )


def upsert_vendor(
    conn: sqlite3.Connection,
    *,
    slug: str,
    display_name: str,
    category: str | None,
    source_doc: str | None,
    is_ghost: bool,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO vendor"
        " (slug, display_name, category, source_doc, is_ghost)"
        " VALUES (?, ?, ?, ?, ?)",
        (slug, display_name, category, source_doc, int(is_ghost)),
    )


def upsert_decision(
    conn: sqlite3.Connection,
    *,
    id: str,
    date: str,
    title: str,
    decision_text: str | None,
    why: str | None,
    alternatives: str | None,
    principle: str | None,
    source_context: str | None,
    log_doc: str,
    status: str,
    parse_status: str,
    format_drift_notes: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO decision"
        " (id, date, title, decision_text, why, alternatives, principle,"
        "  source_context, log_doc, status, parse_status, format_drift_notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (id, date, title, decision_text, why, alternatives, principle,
         source_context, log_doc, status, parse_status, format_drift_notes),
    )


def upsert_edge(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_id: str,
    edge_type: str,
    target_type: str,
    target_id: str,
    edge_subtype: str = "",
    confidence: str | None = None,
    context: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO edge"
        " (source_type, source_id, edge_type, target_type, target_id,"
        "  edge_subtype, confidence, context)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source_type, source_id, edge_type, target_type, target_id,
         edge_subtype, confidence, context),
    )


def upsert_broken_link(
    conn: sqlite3.Connection,
    *,
    source_doc: str,
    link_text: str,
    link_target: str,
    detected_at: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO broken_link"
        " (source_doc, link_text, link_target, detected_at)"
        " VALUES (?, ?, ?, ?)",
        (source_doc, link_text, link_target, detected_at),
    )


def upsert_format_drift(
    conn: sqlite3.Connection,
    *,
    artifact_kind: str,
    artifact_id: str,
    detected_at: str,
    notes: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO format_drift"
        " (artifact_kind, artifact_id, detected_at, notes)"
        " VALUES (?, ?, ?, ?)",
        (artifact_kind, artifact_id, detected_at, notes),
    )


def upsert_doc_fts(
    conn: sqlite3.Connection,
    *,
    path: str,
    title: str | None,
    body: str,
) -> None:
    conn.execute("DELETE FROM doc_fts WHERE path = ?", (path,))
    conn.execute(
        "INSERT INTO doc_fts (path, title, body) VALUES (?, ?, ?)",
        (path, title or "", body),
    )


def update_meta(conn: sqlite3.Connection, *, built_at: str, build_kind: str) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
        [("built_at", built_at), ("build_kind", build_kind)],
    )
