"""FastMCP server — twelve tools over stdio (default) or a shared HTTP server.

Eight graph tools (Phase 3) plus four semantic-layer tools (plan.md Phase 8):
semantic_search, read_document, save_memory, recall_memories. The semantic
tools load their models lazily, so graph-only use never pays for them.
"""

from __future__ import annotations

from dataclasses import asdict

from fastmcp import FastMCP

from cowork_graph import config as cfg_mod
from cowork_graph import db, queries

mcp = FastMCP("cowork-graph")


def _conn():
    cfg = cfg_mod.load()
    return db.connect(cfg.db_path)


def _bounded(items: list, total: int, hint: str) -> dict:
    """Wrap a limited result list in the {items, total, note?} envelope.

    Item dicts keep the exact query-layer shape; the envelope exists so a
    truncated response says it was truncated instead of silently looking
    complete — an unbounded list_active response has hit 55k+ characters.
    """
    resp: dict = {"items": [asdict(i) for i in items], "total": total}
    if total > len(items):
        resp["note"] = f"...and {total - len(items)} more; {hint}"
    return resp


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
    limit: int = 50,
    count_only: bool = False,
) -> dict:
    """List effectively-active docs across the corpus (a leaf tagged status/active or untagged takes its project hub's status, so leaves of a paused or parked project drop out; every `status` field in responses is this effective status), newest-first by last_modified, optionally filtered by entity scope (autoscriptstudio, personal, nexus-legacy-holdings) or project slug. Use when surveying current work, generating a status summary, or planning a work session by seeing what's in flight. Returns {items, total} plus a note when truncated at `limit` (default 50); pass count_only=true for just the total."""
    conn = _conn()
    try:
        total = queries.count_active(conn, scope=scope, project=project)
        if count_only:
            return {"items": [], "total": total}
        items = queries.list_active(conn, scope=scope, project=project, limit=limit)
    finally:
        conn.close()
    return _bounded(items, total, "raise limit or narrow by scope/project")


@mcp.tool
def list_blocked(limit: int = 50, count_only: bool = False) -> dict:
    """List effectively-blocked docs, newest-first, with the upstream blocker resolved via BLOCKS edges. Use when investigating what's stuck and why, generating a blockers list, or before making decisions that depend on currently-blocked work clearing. Returns {items, total} plus a note when truncated at `limit` (default 50); pass count_only=true for just the total."""
    conn = _conn()
    try:
        total = queries.count_blocked(conn)
        if count_only:
            return {"items": [], "total": total}
        items = queries.list_blocked(conn, limit=limit)
    finally:
        conn.close()
    return _bounded(items, total, "raise limit")


@mcp.tool
def project_state(slug: str) -> queries.ProjectState | None:
    """Full subgraph for one project: hub doc, member docs, status mix (active/blocked/done counts), blockers, recent activity, related decisions. Use when zooming into a single project to understand its current shape, surveying what's left to do, or generating a project status report."""
    conn = _conn()
    try:
        return queries.project_state(conn, slug)
    finally:
        conn.close()


@mcp.tool
def who(name: str, mentions_limit: int = 50) -> dict | None:
    """Person profile: canonical info plus docs that mention them, newest-first. Use when looking up someone's role and context, finding all places they're referenced, or pulling background on a person before a meeting or message. `mentions` is truncated at `mentions_limit` (default 50) with `mentions_total` always the full count and a note when truncated."""
    conn = _conn()
    try:
        profile = queries.who(conn, name, mentions_limit=mentions_limit)
    finally:
        conn.close()
    if profile is None:
        return None
    resp = asdict(profile)
    if profile.mentions_total > len(profile.mentions):
        resp["note"] = (
            f"...and {profile.mentions_total - len(profile.mentions)} more mentions;"
            " raise mentions_limit"
        )
    return resp


@mcp.tool
def decisions(
    topic: str | None = None,
    since: str | None = None,
    limit: int = 50,
    count_only: bool = False,
) -> dict:
    """Recent decisions from memory/decisions-log.md, newest-first, optionally topic-filtered or since a date, with SUPERSEDES and ABOUT_DECISION edges resolved. Use when needing decision history, looking up the rationale behind a past choice, surveying strategic context, or checking whether something is governed by an existing decision. Returns {items, total} plus a note when truncated at `limit` (default 50); pass count_only=true for just the total."""
    conn = _conn()
    try:
        total = queries.count_decisions(conn, topic=topic, since=since)
        if count_only:
            return {"items": [], "total": total}
        items = queries.decisions(conn, topic=topic, since=since, limit=limit)
    finally:
        conn.close()
    return _bounded(items, total, "raise limit or narrow by topic/since")


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


# ---------------------------------------------------------------------------
# Semantic layer (Phase 8)
# ---------------------------------------------------------------------------


def _semantic():
    from cowork_graph import semantic

    if not semantic.available():
        raise RuntimeError(
            f"semantic layer not installed on this server — {semantic.MISSING_DEPS_HINT}"
        )
    from cowork_graph.semantic.models import shared
    from cowork_graph.semantic.pii import Detector

    cfg = cfg_mod.load()
    return (
        cfg,
        shared(cfg.semantic.model_cache),
        Detector(cfg.semantic.pii_detect, cfg.semantic.pii_device),
    )


@mcp.tool
def semantic_search(
    query: str,
    scope: str | None = None,
    project: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    path_prefix: str | None = None,
    since: str | None = None,
    limit: int = 8,
) -> dict:
    """Meaning-based search over the whole cowork corpus — markdown plus PDFs, Word/Excel files and OCR'd scans and images — blending vector similarity with keyword matching and a cross-encoder rerank. Use when you need passages that answer a question, not just docs whose words match (search_docs is the exact-word title/body search). Each hit carries the graph's effective status and projects for markdown docs. Filters: scope (entity folder, e.g. personal; prefix with '-' to exclude, e.g. '-personal'), project slug, status (effective status — markdown only), kind (md, pdf, image, office, text), path_prefix, since (ISO date, last commit or modification). Returns up to `limit` (default 8, max 30) passages, at most 3 per file, with 0–1 scores; scores under ~0.5 usually mean nothing relevant was found. Recorded identifiers (account numbers, keys, SSNs, contact details) appear masked as [pii …last4]."""
    from cowork_graph.semantic import search

    cfg, models, _ = _semantic()
    return search.search(
        cfg,
        models,
        query,
        scope=scope,
        project=project,
        status=status,
        kind=kind,
        path_prefix=path_prefix,
        since=since,
        limit=limit,
    ).as_dict()


@mcp.tool
def read_document(path: str, page: int = 1) -> dict:
    """Read the extracted text of one indexed corpus file by its cowork-relative path (as returned by semantic_search), including OCR'd PDFs and images that cannot be read as plain files. Long files are paged in ~40k-character windows; pass page=2, 3, ... to continue. Only files in the semantic index are readable. Recorded identifiers are masked as [pii …last4]."""
    from cowork_graph.semantic import search

    cfg, _, _ = _semantic()
    return search.read_document(cfg, path, page=page)


@mcp.tool
def save_memory(text: str, title: str | None = None, tags: list[str] | None = None) -> dict:
    """Save a durable note for future sessions (a decision, a preference, a project fact) as a markdown file under memory/saved/ in the cowork corpus, indexed immediately so recall_memories finds it. Keep it self-contained and specific; include dates. Notes containing raw identifiers (account or card numbers, SSNs, keys) are refused — keep at most the last four digits. Optional tags use the corpus convention axis/value, e.g. project/kalshicaster. The file is left uncommitted; it syncs to the other machine on Joe's next cowork commit."""
    from cowork_graph.semantic import memory

    cfg, models, detector = _semantic()
    return memory.save_memory(cfg, models, detector, text, title=title, tags=tags)


@mcp.tool
def recall_memories(query: str, limit: int = 3) -> dict:
    """Find previously saved memories (notes written by save_memory) relevant to a question, best first, with 0–1 scores. Use at the start of a task to pick up context earlier sessions recorded. For the wider corpus use semantic_search."""
    from cowork_graph.semantic import memory

    cfg, models, _ = _semantic()
    return memory.recall(cfg, models, query, limit=limit)


def _warm_semantic() -> None:
    """Load the retrieval models in the background so the first search is fast."""
    import threading

    def warm() -> None:
        try:
            cfg, models, _ = _semantic()
            if cfg.semantic.db_path.exists():
                models.warm()
        except Exception as exc:  # noqa: BLE001 — best effort; tool calls report real errors
            import sys

            print(f"semantic warm-up skipped: {exc}", file=sys.stderr)

    threading.Thread(target=warm, name="semantic-warm", daemon=True).start()


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

    HTTP runs stateless: every tool call opens its own DB connection, so
    per-session server state buys nothing — and sessionful streamable HTTP
    means a server restart invalidates every connected client's session ID.
    The long-lived mcp-remote proxy inside the jl-graph desktop extension
    then wedges on 404 "Session not found" (its reconnect loop reuses the
    dead session) and every tool call hangs until the extension is restarted.
    Stateless mode ignores session IDs entirely, so clients ride through
    server restarts.
    """
    if transport == "http":
        _warm_semantic()
        mcp.run(transport="http", host=host, port=port, path=path, stateless_http=True)
    else:
        mcp.run()
