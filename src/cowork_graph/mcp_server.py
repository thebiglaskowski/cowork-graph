"""FastMCP server — eight tools, stdio transport only."""

from __future__ import annotations

from fastmcp import FastMCP

from cowork_graph import config as cfg_mod
from cowork_graph import db, queries

mcp = FastMCP("cowork-graph")


def _conn():
    cfg = cfg_mod.load()
    return db.connect(cfg.db_path)


@mcp.tool
def search_docs(
    query: str,
    tags: list[str] | None = None,
    status: str | None = None,
    scope: str | None = None,
) -> list[queries.DocHit]:
    """Full-text search over the cowork corpus."""
    conn = _conn()
    try:
        return queries.search_docs(conn, query, tags=tags, status=status, scope=scope)
    finally:
        conn.close()


@mcp.tool
def get_doc(path: str) -> queries.DocDetail | None:
    """Doc metadata and edge neighborhood."""
    conn = _conn()
    try:
        return queries.get_doc(conn, path)
    finally:
        conn.close()


@mcp.tool
def list_active(
    scope: str | None = None,
    project: str | None = None,
) -> list[queries.DocSummary]:
    """All active docs, optionally filtered by entity scope or project slug."""
    conn = _conn()
    try:
        return queries.list_active(conn, scope=scope, project=project)
    finally:
        conn.close()


@mcp.tool
def list_blocked() -> list[queries.BlockedDoc]:
    """All blocked docs with upstream blockers resolved via BLOCKS edges."""
    conn = _conn()
    try:
        return queries.list_blocked(conn)
    finally:
        conn.close()


@mcp.tool
def project_state(slug: str) -> queries.ProjectState | None:
    """Full subgraph for a project: hub, members, status mix, blockers, decisions."""
    conn = _conn()
    try:
        return queries.project_state(conn, slug)
    finally:
        conn.close()


@mcp.tool
def who(name: str) -> queries.PersonProfile | None:
    """Person node and all docs mentioning them (v1: full canonical name only)."""
    conn = _conn()
    try:
        return queries.who(conn, name)
    finally:
        conn.close()


@mcp.tool
def decisions(
    topic: str | None = None,
    since: str | None = None,
) -> list[queries.DecisionEntry]:
    """Recent decisions, optionally filtered by topic keyword or date (YYYY-MM-DD)."""
    conn = _conn()
    try:
        return queries.decisions(conn, topic=topic, since=since)
    finally:
        conn.close()


@mcp.tool
def audit(write_report: bool = False) -> dict:
    """Run all ten drift-detection checks. Optionally write a markdown report to the cowork audits dir."""
    cfg = cfg_mod.load()
    report_dir = cfg.cowork_root / "claude-environment/cowork-graph/audits" if write_report else None
    conn = _conn()
    try:
        return queries.audit(
            conn,
            write_report=write_report,
            report_dir=report_dir,
            suppressions_path=cfg.suppressions_path,
        )
    finally:
        conn.close()


def main() -> None:
    mcp.run()
