"""FastMCP server — eight tools over stdio (default) or a shared HTTP server."""

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
    """Full-text search across the cowork corpus, with optional filters for tags, status, and entity scope. Use when looking for a doc by topic or partial content, surveying coverage of a subject across the corpus, or locating a specific doc whose path you don't remember. Returns paths, titles, and snippets."""
    conn = _conn()
    try:
        return queries.search_docs(conn, query, tags=tags, status=status, scope=scope)
    finally:
        conn.close()


@mcp.tool
def get_doc(path: str) -> queries.DocDetail | None:
    """Get full structured info for one doc: frontmatter, neighbors via LINKS_TO/RELATED_TO/BLOCKS edges in both directions, last-modified time. Use when zooming into a specific doc to understand its position in the graph — what links to it, what it links out to — before editing it or making decisions that touch it."""
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
    """List every doc with status/active across the corpus, optionally filtered by entity scope (autoscriptstudio, personal, nexus-legacy-holdings) or project slug. Use when surveying current work, generating a status summary, or planning a work session by seeing what's in flight."""
    conn = _conn()
    try:
        return queries.list_active(conn, scope=scope, project=project)
    finally:
        conn.close()


@mcp.tool
def list_blocked() -> list[queries.BlockedDoc]:
    """List every doc with status/blocked, with the upstream blocker resolved via BLOCKS edges. Use when investigating what's stuck and why, generating a blockers list, or before making decisions that depend on currently-blocked work clearing."""
    conn = _conn()
    try:
        return queries.list_blocked(conn)
    finally:
        conn.close()


@mcp.tool
def project_state(slug: str) -> queries.ProjectState | None:
    """Full subgraph for one project: hub doc, member docs, status mix (active/blocked/done counts), blockers, recent activity, related decisions. Use when zooming into a single project to understand its current shape, surveying what's left to do, or generating a project status report."""
    conn = _conn()
    try:
        return queries.project_state(conn, slug)
    finally:
        conn.close()


@mcp.tool
def who(name: str) -> queries.PersonProfile | None:
    """Person profile: canonical info plus every doc that mentions them. Use when looking up someone's role and context, finding all places they're referenced, or pulling background on a person before a meeting or message."""
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
    """Recent decisions from memory/decisions-log.md, optionally topic-filtered or since a date, with SUPERSEDES and ABOUT_DECISION edges resolved. Use when needing decision history, looking up the rationale behind a past choice, surveying strategic context, or checking whether something is governed by an existing decision."""
    conn = _conn()
    try:
        return queries.decisions(conn, topic=topic, since=since)
    finally:
        conn.close()


@mcp.tool
def audit(write_report: bool = False) -> dict:
    """Run all ten drift-detection checks against the corpus, optionally writing a markdown report to the audits folder. Use after bulk corpus edits to verify health, when investigating whether something has drifted from convention, before a release or major reorganization, or as part of weekly maintenance. Findings include broken links, ghost projects, orphan docs, format drift, decision drift, tag drift, and more."""
    cfg = cfg_mod.load()
    report_dir = (
        cfg.cowork_root / "claude-environment/cowork-graph/audits" if write_report else None
    )
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


def main(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    path: str = "/mcp",
) -> None:
    """Run the MCP server.

    Default ``stdio`` — one process per client, spawned by the MCP client.
    Pass ``transport="http"`` to run a single shared server that multiple
    clients connect to over HTTP, avoiding a per-client process spawn (and the
    startup contention that causes).
    """
    if transport == "http":
        mcp.run(transport="http", host=host, port=port, path=path)
    else:
        mcp.run()
