"""Pure data-layer query functions for MCP tools. No MCP awareness here."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class DocHit:
    path: str
    title: str | None
    snippet: str
    score: float


@dataclass
class DocSummary:
    path: str
    title: str | None
    status: str | None
    doc_type: str | None
    last_modified: str | None


@dataclass
class DocDetail:
    path: str
    title: str | None
    status: str | None
    doc_type: str | None
    word_count: int
    last_modified: str | None
    outbound: list[dict]
    inbound: list[dict]


@dataclass
class BlockedDoc:
    path: str
    title: str | None
    blockers: list[str]


@dataclass
class DecisionEntry:
    id: str
    date: str
    title: str
    decision_text: str | None
    why: str | None
    alternatives: str | None
    principle: str | None
    source_context: str | None
    status: str
    about_docs: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)


@dataclass
class ProjectState:
    slug: str
    hub_doc: str | None
    member_docs: list[DocSummary]
    status_mix: dict[str, int]
    blockers: list[BlockedDoc]
    related_decisions: list[DecisionEntry]


@dataclass
class PersonProfile:
    slug: str
    display_name: str
    role: str | None
    source_doc: str | None
    mentions: list[DocSummary]


# ---------------------------------------------------------------------------
# Query functions — one per MCP tool
# ---------------------------------------------------------------------------


def search_docs(
    conn: sqlite3.Connection,
    query: str,
    *,
    tags: list[str] | None = None,
    status: str | None = None,
    scope: str | None = None,
    limit: int = 20,
) -> list[DocHit]:
    """FTS5 full-text search, optionally filtered by tag, status, or path prefix."""
    sql = (
        "SELECT d.path, d.title,"
        " snippet(doc_fts, 2, '[', ']', '...', 10) AS snippet,"
        " bm25(doc_fts) AS score"
        " FROM doc_fts"
        " JOIN doc d ON d.path = doc_fts.path"
        " WHERE doc_fts MATCH ?"
    )
    params: list = [query]

    if status:
        sql += " AND d.status = ?"
        params.append(status)
    if scope:
        sql += " AND d.path LIKE ?"
        params.append(f"{scope.rstrip('/')}/%")
    if tags:
        for tag in tags:
            sql += (
                " AND EXISTS ("
                "  SELECT 1 FROM edge"
                "  WHERE source_type='doc' AND source_id=d.path"
                "  AND edge_type='TAGGED' AND target_id=?"
                ")"
            )
            params.append(tag)

    sql += " ORDER BY bm25(doc_fts) LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [
        DocHit(
            path=r["path"],
            title=r["title"],
            snippet=r["snippet"] or "",
            score=r["score"],
        )
        for r in rows
    ]


def get_doc(conn: sqlite3.Connection, path: str) -> DocDetail | None:
    """Return doc metadata plus inbound and outbound edges."""
    row = conn.execute(
        "SELECT path, title, status, doc_type, word_count, last_modified"
        " FROM doc WHERE path = ?",
        (path,),
    ).fetchone()
    if row is None:
        return None

    outbound = [
        {
            "edge_type": r["edge_type"],
            "edge_subtype": r["edge_subtype"],
            "target_type": r["target_type"],
            "target_id": r["target_id"],
        }
        for r in conn.execute(
            "SELECT edge_type, edge_subtype, target_type, target_id FROM edge"
            " WHERE source_type='doc' AND source_id=?",
            (path,),
        )
    ]
    inbound = [
        {
            "edge_type": r["edge_type"],
            "edge_subtype": r["edge_subtype"],
            "source_type": r["source_type"],
            "source_id": r["source_id"],
        }
        for r in conn.execute(
            "SELECT edge_type, edge_subtype, source_type, source_id FROM edge"
            " WHERE target_type='doc' AND target_id=?",
            (path,),
        )
    ]

    return DocDetail(
        path=row["path"],
        title=row["title"],
        status=row["status"],
        doc_type=row["doc_type"],
        word_count=row["word_count"],
        last_modified=row["last_modified"],
        outbound=outbound,
        inbound=inbound,
    )


def list_active(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    project: str | None = None,
) -> list[DocSummary]:
    """All active docs, optionally narrowed by entity scope or project slug."""
    sql = (
        "SELECT path, title, status, doc_type, last_modified"
        " FROM doc WHERE status='active'"
    )
    params: list = []

    if scope:
        sql += " AND path LIKE ?"
        params.append(f"{scope.rstrip('/')}/%")
    if project:
        sql += (
            " AND EXISTS ("
            "  SELECT 1 FROM edge"
            "  WHERE source_type='doc' AND source_id=doc.path"
            "  AND edge_type='MEMBER_OF_PROJECT' AND target_id=?"
            ")"
        )
        params.append(project)

    sql += " ORDER BY last_modified DESC"
    rows = conn.execute(sql, params).fetchall()
    return [
        DocSummary(
            path=r["path"],
            title=r["title"],
            status=r["status"],
            doc_type=r["doc_type"],
            last_modified=r["last_modified"],
        )
        for r in rows
    ]


def list_blocked(conn: sqlite3.Connection) -> list[BlockedDoc]:
    """All blocked docs with upstream blockers resolved via BLOCKS edges."""
    blocked_rows = conn.execute(
        "SELECT path, title FROM doc WHERE status='blocked'"
    ).fetchall()

    result = []
    for row in blocked_rows:
        path = row["path"]
        # Docs that explicitly downstream-block this doc
        blocker_paths = [
            r["source_id"]
            for r in conn.execute(
                "SELECT source_id FROM edge"
                " WHERE target_type='doc' AND target_id=?"
                " AND edge_type='BLOCKS' AND edge_subtype='downstream'",
                (path,),
            )
        ]
        # Docs this doc named as its upstream dependencies
        blocker_paths += [
            r["target_id"]
            for r in conn.execute(
                "SELECT target_id FROM edge"
                " WHERE source_type='doc' AND source_id=?"
                " AND edge_type='BLOCKS' AND edge_subtype='upstream'",
                (path,),
            )
        ]
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_blockers = [p for p in blocker_paths if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
        result.append(BlockedDoc(path=path, title=row["title"], blockers=unique_blockers))

    return result


def project_state(conn: sqlite3.Connection, slug: str) -> ProjectState | None:
    """Full subgraph for a project: hub, members, status mix, blockers, decisions."""
    row = conn.execute(
        "SELECT slug, hub_doc FROM project WHERE slug = ?", (slug,)
    ).fetchone()
    if row is None:
        return None

    member_rows = conn.execute(
        "SELECT d.path, d.title, d.status, d.doc_type, d.last_modified"
        " FROM doc d"
        " JOIN edge e ON e.source_type='doc' AND e.source_id=d.path"
        " AND e.edge_type='MEMBER_OF_PROJECT' AND e.target_id=?"
        " ORDER BY d.last_modified DESC",
        (slug,),
    ).fetchall()
    member_docs = [
        DocSummary(
            path=r["path"],
            title=r["title"],
            status=r["status"],
            doc_type=r["doc_type"],
            last_modified=r["last_modified"],
        )
        for r in member_rows
    ]

    status_mix: dict[str, int] = {}
    for doc in member_docs:
        key = doc.status or "unknown"
        status_mix[key] = status_mix.get(key, 0) + 1

    blockers = []
    for doc in member_docs:
        if doc.status != "blocked":
            continue
        upstream = [
            r["source_id"]
            for r in conn.execute(
                "SELECT source_id FROM edge WHERE target_type='doc' AND target_id=?"
                " AND edge_type='BLOCKS' AND edge_subtype='downstream'",
                (doc.path,),
            )
        ] + [
            r["target_id"]
            for r in conn.execute(
                "SELECT target_id FROM edge WHERE source_type='doc' AND source_id=?"
                " AND edge_type='BLOCKS' AND edge_subtype='upstream'",
                (doc.path,),
            )
        ]
        seen_b: set[str] = set()
        unique_up = [p for p in upstream if not (p in seen_b or seen_b.add(p))]  # type: ignore[func-returns-value]
        blockers.append(BlockedDoc(path=doc.path, title=doc.title, blockers=unique_up))

    if member_docs:
        placeholders = ",".join("?" * len(member_docs))
        dec_rows = conn.execute(
            f"SELECT DISTINCT d.id, d.date, d.title, d.decision_text, d.why,"
            f" d.alternatives, d.principle, d.source_context, d.status"
            f" FROM decision d"
            f" JOIN edge e ON e.source_type='decision' AND e.source_id=d.id"
            f" AND e.edge_type='ABOUT_DECISION' AND e.target_type='doc'"
            f" WHERE e.target_id IN ({placeholders})"
            f" ORDER BY d.date DESC",
            [d.path for d in member_docs],
        ).fetchall()
        related_decisions = [_decision_entry(conn, r) for r in dec_rows]
    else:
        related_decisions = []

    return ProjectState(
        slug=slug,
        hub_doc=row["hub_doc"],
        member_docs=member_docs,
        status_mix=status_mix,
        blockers=blockers,
        related_decisions=related_decisions,
    )


def who(conn: sqlite3.Connection, name: str) -> PersonProfile | None:
    """Person node plus every doc that mentions them. v1: full canonical name only."""
    row = conn.execute(
        "SELECT slug, display_name, role, source_doc FROM person"
        " WHERE display_name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row is None:
        return None

    slug = row["slug"]
    mention_rows = conn.execute(
        "SELECT d.path, d.title, d.status, d.doc_type, d.last_modified"
        " FROM doc d"
        " JOIN edge e ON e.source_type='doc' AND e.source_id=d.path"
        " AND e.edge_type='MENTIONS' AND e.target_type='person' AND e.target_id=?",
        (slug,),
    ).fetchall()
    mentions = [
        DocSummary(
            path=r["path"],
            title=r["title"],
            status=r["status"],
            doc_type=r["doc_type"],
            last_modified=r["last_modified"],
        )
        for r in mention_rows
    ]

    return PersonProfile(
        slug=slug,
        display_name=row["display_name"],
        role=row["role"],
        source_doc=row["source_doc"],
        mentions=mentions,
    )


def decisions(
    conn: sqlite3.Connection,
    *,
    topic: str | None = None,
    since: str | None = None,
) -> list[DecisionEntry]:
    """Recent decisions, optionally filtered by date and topic keyword."""
    sql = (
        "SELECT id, date, title, decision_text, why, alternatives,"
        " principle, source_context, status"
        " FROM decision WHERE 1=1"
    )
    params: list = []

    if since:
        sql += " AND date >= ?"
        params.append(since)
    if topic:
        sql += " AND (title LIKE ? OR decision_text LIKE ?)"
        params.extend([f"%{topic}%", f"%{topic}%"])

    sql += " ORDER BY date DESC"
    rows = conn.execute(sql, params).fetchall()
    return [_decision_entry(conn, r) for r in rows]


def audit(
    conn: sqlite3.Connection,
    *,
    write_report: bool = False,
    report_dir=None,
    suppressions_path=None,
) -> dict:
    """Run all ten drift-detection checks and return structured findings."""
    from cowork_graph import audit as audit_mod
    return audit_mod.run_audit(
        conn,
        write_report=write_report,
        report_dir=report_dir,
        suppressions_path=suppressions_path,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decision_entry(conn: sqlite3.Connection, row: sqlite3.Row) -> DecisionEntry:
    dec_id = row["id"]
    about_docs = [
        r["target_id"]
        for r in conn.execute(
            "SELECT target_id FROM edge"
            " WHERE source_type='decision' AND source_id=? AND edge_type='ABOUT_DECISION'",
            (dec_id,),
        )
    ]
    supersedes = [
        r["target_id"]
        for r in conn.execute(
            "SELECT target_id FROM edge"
            " WHERE source_type='decision' AND source_id=? AND edge_type='SUPERSEDES'",
            (dec_id,),
        )
    ]
    superseded_by = [
        r["source_id"]
        for r in conn.execute(
            "SELECT source_id FROM edge"
            " WHERE target_type='decision' AND target_id=? AND edge_type='SUPERSEDES'",
            (dec_id,),
        )
    ]
    return DecisionEntry(
        id=row["id"],
        date=row["date"],
        title=row["title"],
        decision_text=row["decision_text"],
        why=row["why"],
        alternatives=row["alternatives"],
        principle=row["principle"],
        source_context=row["source_context"],
        status=row["status"],
        about_docs=about_docs,
        supersedes=supersedes,
        superseded_by=superseded_by,
    )
