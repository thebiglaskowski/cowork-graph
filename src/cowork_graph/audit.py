"""Audit / linter: ten drift-detection checks against the graph DB.

v0 interpretation notes (review during Phase 6 trust window):
- one_way_edges: checks sibling-subtype RELATED_TO only; other Related-block
  subtypes (parent_hub, downstream, upstream) are intentionally directional.
- inconsistent_hub_state: hub doc with BLOCKS(downstream) targets whose own
  status is done/blocked. The plan's text could also mean text-content member
  lists, but those aren't parsed as structured data in v1.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Individual check functions — each returns list[dict], caller manages conn.
# ---------------------------------------------------------------------------


def ghost_projects(conn: sqlite3.Connection) -> list[dict]:
    """project/<slug> tags in frontmatter with no hub doc registered."""
    rows = conn.execute(
        "SELECT slug FROM project WHERE is_ghost=1 ORDER BY slug"
    ).fetchall()
    return [{"slug": r["slug"]} for r in rows]


def ghost_people(conn: sqlite3.Connection, *, min_mentions: int = 2) -> list[dict]:
    """Names mentioned in min_mentions+ docs but no memory/people/ file."""
    rows = conn.execute(
        "SELECT p.slug, p.display_name, COUNT(DISTINCT e.source_id) AS mention_count"
        " FROM person p"
        " JOIN edge e ON e.target_type='person' AND e.target_id=p.slug"
        "  AND e.edge_type='MENTIONS'"
        " WHERE p.is_ghost=1"
        " GROUP BY p.slug"
        " HAVING COUNT(DISTINCT e.source_id) >= ?"
        " ORDER BY mention_count DESC, p.slug",
        (min_mentions,),
    ).fetchall()
    return [
        {
            "slug": r["slug"],
            "display_name": r["display_name"],
            "mention_count": r["mention_count"],
        }
        for r in rows
    ]


def broken_links(conn: sqlite3.Connection) -> list[dict]:
    """Markdown links whose targets don't resolve to real files."""
    rows = conn.execute(
        "SELECT source_doc, link_text, link_target"
        " FROM broken_link ORDER BY source_doc"
    ).fetchall()
    return [
        {
            "source_doc": r["source_doc"],
            "link_text": r["link_text"],
            "link_target": r["link_target"],
        }
        for r in rows
    ]


def one_way_edges(conn: sqlite3.Connection) -> list[dict]:
    """Sibling RELATED_TO edges with no reverse direction (v0: sibling subtype only)."""
    rows = conn.execute(
        "SELECT e.source_id AS doc_a, e.target_id AS doc_b FROM edge e"
        " WHERE e.source_type='doc' AND e.target_type='doc'"
        " AND e.edge_type='RELATED_TO' AND e.edge_subtype='sibling'"
        " AND NOT EXISTS ("
        "  SELECT 1 FROM edge r"
        "  WHERE r.source_type='doc' AND r.source_id=e.target_id"
        "  AND r.target_type='doc' AND r.target_id=e.source_id"
        "  AND r.edge_type='RELATED_TO'"
        " )"
        " ORDER BY doc_a"
    ).fetchall()
    return [{"doc_a": r["doc_a"], "doc_b": r["doc_b"]} for r in rows]


def stale_active_docs(conn: sqlite3.Connection, *, days: int = 90) -> list[dict]:
    """status/active docs with no filesystem edits in the last `days` days."""
    rows = conn.execute(
        "SELECT path, title, last_modified FROM doc"
        " WHERE status='active'"
        " AND last_modified IS NOT NULL"
        " AND substr(last_modified, 1, 10) < date('now', ? || ' days')"
        " ORDER BY last_modified ASC",
        (f"-{days}",),
    ).fetchall()
    return [
        {"path": r["path"], "title": r["title"], "last_modified": r["last_modified"]}
        for r in rows
    ]


def inconsistent_hub_state(conn: sqlite3.Connection) -> list[dict]:
    """Active hub docs with BLOCKS(downstream) targets whose own status is done/blocked."""
    rows = conn.execute(
        "SELECT h.path AS hub, h.title AS hub_title,"
        " m.path AS member, m.title AS member_title, m.status AS member_status"
        " FROM doc h"
        " JOIN edge e ON e.source_type='doc' AND e.source_id=h.path"
        "  AND e.edge_type='BLOCKS' AND e.edge_subtype='downstream'"
        " JOIN doc m ON m.path=e.target_id"
        " WHERE h.doc_type='hub' AND h.status='active'"
        " AND m.status IN ('done', 'blocked')"
        " ORDER BY h.path, m.path"
    ).fetchall()
    return [
        {
            "hub": r["hub"],
            "hub_title": r["hub_title"],
            "member": r["member"],
            "member_title": r["member_title"],
            "member_status": r["member_status"],
        }
        for r in rows
    ]


def orphan_docs(conn: sqlite3.Connection) -> list[dict]:
    """Active non-hub docs with no inbound LINKS_TO, RELATED_TO, or BLOCKS edges."""
    rows = conn.execute(
        "SELECT d.path, d.title FROM doc d"
        " WHERE d.status='active'"
        " AND d.doc_type IS NOT 'hub'"
        " AND NOT EXISTS ("
        "  SELECT 1 FROM edge e"
        "  WHERE e.target_type='doc' AND e.target_id=d.path"
        "  AND e.edge_type IN ('LINKS_TO', 'RELATED_TO', 'BLOCKS')"
        " )"
        " ORDER BY d.path"
    ).fetchall()
    return [{"path": r["path"], "title": r["title"]} for r in rows]


def decision_drift(conn: sqlite3.Connection) -> list[dict]:
    """Active decisions with no ABOUT_DECISION edge to any doc."""
    rows = conn.execute(
        "SELECT d.id, d.date, d.title FROM decision d"
        " WHERE d.status='active'"
        " AND NOT EXISTS ("
        "  SELECT 1 FROM edge e"
        "  WHERE e.source_type='decision' AND e.source_id=d.id"
        "  AND e.edge_type='ABOUT_DECISION'"
        " )"
        " ORDER BY d.date DESC"
    ).fetchall()
    return [{"id": r["id"], "date": r["date"], "title": r["title"]} for r in rows]


def tag_drift(conn: sqlite3.Connection, *, max_count: int = 2) -> list[dict]:
    """Tags used by <= max_count docs — likely typos or one-off conventions."""
    rows = conn.execute(
        "SELECT t.tag, COUNT(e.source_id) AS doc_count"
        " FROM tag t"
        " JOIN edge e ON e.target_type='tag' AND e.target_id=t.tag"
        "  AND e.edge_type='TAGGED'"
        " GROUP BY t.tag"
        " HAVING COUNT(e.source_id) <= ?"
        " ORDER BY COUNT(e.source_id) ASC, t.tag ASC",
        (max_count,),
    ).fetchall()
    return [{"tag": r["tag"], "doc_count": r["doc_count"]} for r in rows]


def decisions_format_drift(conn: sqlite3.Connection) -> list[dict]:
    """Decisions-log entries that don't match the documented Format section."""
    rows = conn.execute(
        "SELECT artifact_id, detected_at, notes"
        " FROM format_drift"
        " WHERE artifact_kind='decision'"
        " ORDER BY artifact_id"
    ).fetchall()
    return [
        {
            "id": r["artifact_id"],
            "detected_at": r["detected_at"],
            "notes": r["notes"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_audit(
    conn: sqlite3.Connection,
    *,
    write_report: bool = False,
    report_dir: Path | None = None,
    stale_days: int = 90,
    ghost_min_mentions: int = 2,
    tag_max_count: int = 2,
    suppressions_path: Path | None = None,
) -> dict:
    """Run all ten checks, return structured findings dict."""
    from cowork_graph.suppressions import filter_findings, load_suppressions

    run_at = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='built_at'"
    ).fetchone()
    built_at = row["value"] if row else None

    suppressions = load_suppressions(suppressions_path) if suppressions_path is not None else set()

    raw: dict[str, list[dict]] = {
        "ghost_projects": ghost_projects(conn),
        "ghost_people": ghost_people(conn, min_mentions=ghost_min_mentions),
        "broken_links": broken_links(conn),
        "one_way_edges": one_way_edges(conn),
        "stale_active_docs": stale_active_docs(conn, days=stale_days),
        "inconsistent_hub_state": inconsistent_hub_state(conn),
        "orphan_docs": orphan_docs(conn),
        "decision_drift": decision_drift(conn),
        "tag_drift": tag_drift(conn, max_count=tag_max_count),
        "decisions_format_drift": decisions_format_drift(conn),
    }

    findings: dict[str, list[dict]] = {}
    suppressed_counts: dict[str, int] = {}
    for rule, items in raw.items():
        filtered, count = filter_findings(rule, items, suppressions)
        findings[rule] = filtered
        if count > 0:
            suppressed_counts[rule] = count

    summary = {k: len(v) for k, v in findings.items()}

    result: dict = {
        "status": "ok",
        "run_at": run_at,
        "built_at": built_at,
        "total_findings": sum(summary.values()),
        "suppressed": suppressed_counts,
        "summary": summary,
        "findings": findings,
        "report_written": None,
    }

    if write_report and report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_path = report_dir / f"{date_str}-audit.md"
        report_path.write_text(format_report(result), encoding="utf-8")
        result["report_written"] = str(report_path)

    return result


# ---------------------------------------------------------------------------
# Markdown report formatter
# ---------------------------------------------------------------------------

_SECTION_LABELS: dict[str, str] = {
    "ghost_projects": "Ghost Projects",
    "ghost_people": "Ghost People",
    "broken_links": "Broken Markdown Links",
    "one_way_edges": "One-Way Sibling Edges",
    "stale_active_docs": "Stale Active Docs",
    "inconsistent_hub_state": "Inconsistent Hub State",
    "orphan_docs": "Orphan Docs",
    "decision_drift": "Decision Drift",
    "tag_drift": "Tag Drift",
    "decisions_format_drift": "Decisions-Log Format Drift",
}


def format_report(result: dict) -> str:
    lines: list[str] = []
    date_str = result["run_at"][:10]
    lines += [
        f"# Cowork Graph Audit — {date_str}",
        "",
        f"**DB built at:** {result['built_at'] or 'unknown'}",
        f"**Audit run at:** {result['run_at']}",
        f"**Total findings:** {result['total_findings']}",
        "",
        "## Summary",
        "",
    ]
    for key, count in result["summary"].items():
        label = _SECTION_LABELS.get(key, key)
        status = "ok" if count == 0 else f"{count} finding(s)"
        lines.append(f"- **{label}:** {status}")
    lines.append("")

    for key, items in result["findings"].items():
        label = _SECTION_LABELS.get(key, key)
        lines.append(f"## {label} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("_No findings._")
        else:
            for item in items:
                lines.append(f"- {_format_finding(key, item)}")
        lines.append("")

    lines += [
        "---",
        "*Generated by cowork-graph audit. Findings are informational —"
        " see [ghost resolution workflow](../plan.md) for triage guidance.*",
        "",
    ]
    return "\n".join(lines)


def _format_finding(check: str, item: dict) -> str:
    if check == "ghost_projects":
        return f"`project/{item['slug']}` — no hub doc registered"
    if check == "ghost_people":
        return (
            f"{item['display_name']} (`{item['slug']}`)"
            f" — {item['mention_count']} mention(s), no people file"
        )
    if check == "broken_links":
        return (
            f"`{item['source_doc']}` links to `{item['link_target']}`"
            f" (text: {item['link_text']!r})"
        )
    if check == "one_way_edges":
        return f"`{item['doc_a']}` — sibling edge to `{item['doc_b']}` has no reverse"
    if check == "stale_active_docs":
        return f"`{item['path']}` — last modified {item['last_modified'][:10]}"
    if check == "inconsistent_hub_state":
        return (
            f"`{item['hub']}` (active hub) downstream target"
            f" `{item['member']}` is `{item['member_status']}`"
        )
    if check == "orphan_docs":
        return f"`{item['path']}` — no inbound edges"
    if check == "decision_drift":
        return f"`{item['id']}` ({item['date']}) — no ABOUT_DECISION edge"
    if check == "tag_drift":
        return f"`{item['tag']}` — {item['doc_count']} doc(s)"
    if check == "decisions_format_drift":
        return f"`{item['id']}` — {item['notes'] or 'format drift detected'}"
    return str(item)
