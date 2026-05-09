"""Self-contained HTML report renderer for cowork-graph audit results.

Takes the result dict from audit.run_audit() and writes a single HTML file
with no external dependencies — all CSS is embedded, no CDN, no JS.
"""

from __future__ import annotations

import html as _html
import os as _os
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

_SEVERITY: dict[str, str] = {
    "broken_links": "high",
    "decisions_format_drift": "high",
    "ghost_projects": "medium",
    "ghost_people": "medium",
    "inconsistent_hub_state": "medium",
    "decision_drift": "medium",
    "stale_active_docs": "low",
    "tag_drift": "low",
    "orphan_docs": "info",
    "one_way_edges": "info",
}

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

_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Georgia, 'Times New Roman', serif;
  background: #16161a; color: #e1e4e8; line-height: 1.65; font-size: 15px;
}
.container { max-width: 880px; margin: 0 auto; padding: 2rem 1.5rem; }
header { border-bottom: 2px solid #2d3138; padding-bottom: 1.5rem; margin-bottom: 2rem; }
header h1 { font-size: 1.6rem; color: #f0f3f6; }
.meta { color: #8b949e; font-size: 0.875rem; margin-top: 0.35rem; }
h2 { font-size: 1.15rem; margin-bottom: 1rem; color: #f0f3f6; }
code {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 0.85em; background: #262830; color: #e1e4e8;
  padding: 0.1em 0.3em; border-radius: 3px;
}
a { color: #58a6ff; } a:visited { color: #a371f7; }

/* Summary pills */
.summary { margin-bottom: 2rem; }
.counts { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.5rem; }
.count-pill {
  display: inline-flex; align-items: center;
  padding: 0.25rem 0.75rem; border-radius: 999px;
  font-size: 0.78rem; font-family: 'SF Mono', Menlo, Consolas, monospace;
  color: white; font-weight: 600; text-decoration: none;
}
.count-pill:link,
.count-pill:visited,
.count-pill:hover,
.count-pill:active { color: white; }
.pill-high { background: #f85149; } .pill-medium { background: #e3b341; }
.pill-low  { background: #d29922; } .pill-info   { background: #8b949e; }
.pill-ok   { background: #3fb950; }

/* Corpus chart */
.corpus-status { margin-top: 1.25rem; }
.corpus-status h3 { margin-bottom: 0.6rem; font-size: 0.9rem; color: #8b949e; font-weight: normal; }
.corpus-status svg { width: 100%; max-width: 500px; display: block; }

/* TOC */
nav.toc {
  background: #1f2128; border: 1px solid #2d3138; border-radius: 6px;
  padding: 1rem 1.5rem; margin-bottom: 2rem;
}
nav.toc h2 { margin-bottom: 0.5rem; }
nav.toc ul { list-style: none; }
nav.toc li { margin: 0.2rem 0; }
nav.toc a { text-decoration: none; } nav.toc a:hover { text-decoration: underline; }

/* Rule sections */
.rule-section {
  margin-bottom: 1.1rem; background: #1f2128;
  border: 1px solid #2d3138; border-radius: 6px; overflow: hidden;
}
.rule-section > summary {
  cursor: pointer; padding: 0.8rem 1.2rem; background: #25272e;
  display: flex; align-items: center; justify-content: space-between;
  list-style: none; user-select: none;
}
.rule-section > summary::-webkit-details-marker { display: none; }
.rule-section > summary::marker { display: none; }
.rule-section > summary:hover { background: #2a2c33; }
.rule-title { font-weight: bold; font-size: 0.95rem; color: #f0f3f6; }
.finding-count {
  font-size: 0.75rem; font-family: 'SF Mono', Menlo, Consolas, monospace;
  padding: 0.2rem 0.6rem; border-radius: 999px; color: white; font-weight: 600;
}
.badge-high { background: #f85149; } .badge-medium { background: #e3b341; }
.badge-low  { background: #d29922; } .badge-info   { background: #8b949e; }
.badge-ok   { background: #3fb950; }

/* Finding cards */
.findings { padding: 0.6rem 1rem 1rem; display: flex; flex-direction: column; gap: 0.55rem; }
.finding {
  padding: 0.8rem 1rem 0.8rem 1.15rem;
  background: #1f2128; border-left: 4px solid #2d3138;
  border-radius: 0 4px 4px 0;
}
.finding.severity-high   { border-left-color: #f85149; background: #2a1c1c; }
.finding.severity-medium { border-left-color: #e3b341; background: #2a241a; }
.finding.severity-low    { border-left-color: #d29922; background: #2a2818; }
.finding.severity-info   { border-left-color: #8b949e; }
.finding-title { margin-bottom: 0.3rem; font-size: 0.95rem; }
.finding-body  { font-size: 0.875rem; color: #8b949e; margin-top: 0.2rem; }
.source-link {
  display: inline-block; margin-top: 0.4rem;
  font-size: 0.78rem; font-family: 'SF Mono', Menlo, Consolas, monospace;
}
.no-findings { padding: 0.75rem 1rem; color: #8b949e; font-style: italic; font-size: 0.875rem; }

/* Footer */
footer {
  margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid #2d3138;
  font-size: 0.78rem; color: #8b949e;
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  display: flex; flex-direction: column; gap: 0.2rem;
}"""

_SVG_COLORS = ["#4a90d9", "#27ae60", "#e07b3a", "#9b59b6", "#e74c3c", "#1abc9c", "#f39c12"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_html_report(
    result: dict,
    db_path: Path,
    output_path: Path,
    *,
    cowork_root: Path | None = None,
) -> None:
    """Render *result* (the dict from run_audit()) to a self-contained HTML file."""
    meta = _query_meta(db_path)
    status_counts = _query_status_counts(db_path)

    _link_ctx: tuple[Path, Path] | None = None
    if cowork_root is not None:
        try:
            output_path.parent.relative_to(cowork_root)  # validate containment
            _link_ctx = (cowork_root, output_path.parent)
        except ValueError:
            pass

    output_path.write_text(
        _render(result, meta, status_counts, _link_ctx),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# DB helpers (read-only, own connection)
# ---------------------------------------------------------------------------


def _query_meta(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT key, value FROM schema_meta").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def _query_status_counts(db_path: Path) -> list[tuple[str, int]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM doc GROUP BY status ORDER BY count DESC"
        ).fetchall()
        return [(r["status"] or "none", r["count"]) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------


def _render(
    result: dict,
    meta: dict[str, str],
    status_counts: list[tuple[str, int]],
    _link_ctx: tuple[Path, Path] | None,
) -> str:
    e = _html.escape
    date_str = result.get("run_at", "")[:10]
    total = result.get("total_findings", 0)
    built_at = result.get("built_at") or meta.get("built_at") or "unknown"
    findings: dict[str, list[dict]] = result.get("findings", {})
    summary: dict[str, int] = result.get("summary", {})
    generated_at = date_str  # derived from result, not datetime.now() — ensures idempotency

    p: list[str] = []

    p += [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"  <title>cowork-graph audit — {e(date_str)}</title>",
        "  <style>",
        _CSS,
        "  </style>",
        "</head>",
        "<body>",
        '<div class="container">',
    ]

    # Header
    p += [
        "<header>",
        "  <h1>cowork-graph audit</h1>",
        (
            f'  <div class="meta">{e(date_str)}'
            f" &nbsp;&middot;&nbsp; graph built at {e(built_at)}"
            f" &nbsp;&middot;&nbsp; {total} finding{'s' if total != 1 else ''}</div>"
        ),
        "</header>",
    ]

    # Summary
    p += ['<section class="summary">', "  <h2>Summary</h2>", '  <div class="counts">']
    for rule, count in summary.items():
        sev = _SEVERITY.get(rule, "info")
        label = _SECTION_LABELS.get(rule, rule)
        pill_cls = f"pill-{'ok' if count == 0 else sev}"
        p.append(
            f'    <a class="count-pill {pill_cls}" href="#{_rule_id(rule)}">{e(label)}: {count}</a>'
        )
    p.append("  </div>")

    # SVG corpus chart
    p += ['  <div class="corpus-status">', "    <h3>Corpus by status</h3>"]
    if status_counts:
        p.append(_build_svg(status_counts))
    p += ["  </div>", "</section>"]

    # TOC
    p += ['<nav class="toc">', "  <h2>Findings</h2>", "  <ul>"]
    for rule in findings:
        count = summary.get(rule, 0)
        label = _SECTION_LABELS.get(rule, rule)
        p.append(f'    <li><a href="#{_rule_id(rule)}">{e(label)} ({count})</a></li>')
    p += ["  </ul>", "</nav>"]

    # Rule sections
    p.append("<main>")
    for rule, items in findings.items():
        sev = _SEVERITY.get(rule, "info")
        label = _SECTION_LABELS.get(rule, rule)
        count = len(items)
        badge_cls = f"badge-{'ok' if count == 0 else sev}"
        badge_txt = "ok" if count == 0 else str(count)
        open_attr = " open" if count > 0 else ""

        p.append(f'  <details class="rule-section" id="{_rule_id(rule)}"{open_attr}>')
        p += [
            "    <summary>",
            f'      <span class="rule-title">{e(label)}</span>',
            f'      <span class="finding-count {badge_cls}">{badge_txt}</span>',
            "    </summary>",
        ]

        if not items:
            p.append('    <div class="no-findings">No findings.</div>')
        else:
            p.append('    <div class="findings">')
            for item in items:
                p.extend(_render_finding(rule, item, sev, _link_ctx))
            p.append("    </div>")

        p.append("  </details>")
    p.append("</main>")

    # Footer
    schema_v = e(meta.get("schema_version", "?"))
    parser_v = e(meta.get("parser_version", "?"))
    build_kind = e(meta.get("build_kind", "?"))
    p += [
        "<footer>",
        (
            f"  <div>schema_version: {schema_v} &middot; parser_version: {parser_v}"
            f" &middot; build_kind: {build_kind} &middot; built_at: {e(built_at)}</div>"
        ),
        f"  <div>generated by cowork-graph audit_html v1, {e(generated_at)}</div>",
        "</footer>",
        "</div>",
        "</body>",
        "</html>",
    ]

    return "\n".join(p)


def _render_finding(
    rule: str,
    item: dict,
    severity: str,
    _link_ctx: tuple[Path, Path] | None,
) -> list[str]:
    e = _html.escape
    title_html, body_html, source_path = _finding_content(rule, item)
    p: list[str] = [f'      <article class="finding severity-{severity}">']
    p.append(f'        <h3 class="finding-title">{title_html}</h3>')
    if body_html:
        p.append(f'        <div class="finding-body">{body_html}</div>')
    if source_path:
        if _link_ctx is not None:
            cowork_root, html_dir = _link_ctx
            href = e(_os.path.relpath(str(cowork_root / source_path), str(html_dir)))
            p.append(f'        <a class="source-link" href="{href}">source: {e(source_path)}</a>')
        else:
            p.append(
                f'        <span class="source-link">source: <code>{e(source_path)}</code></span>'
            )
    p.append("      </article>")
    return p


def _finding_content(rule: str, item: dict) -> tuple[str, str, str | None]:
    """Return (title_html, body_html, source_path_or_None) for one finding."""
    e = _html.escape

    if rule == "ghost_projects":
        return f"<code>project/{e(item['slug'])}</code>", "No hub doc registered", None

    if rule == "ghost_people":
        return (
            f"{e(item['display_name'])} <code>({e(item['slug'])})</code>",
            f"{item['mention_count']} mention(s), no people file",
            None,
        )

    if rule == "broken_links":
        body = (
            f"links to <code>{e(item['link_target'])}</code> (text: {e(repr(item['link_text']))})"
        )
        return f"<code>{e(item['source_doc'])}</code>", body, item["source_doc"]

    if rule == "one_way_edges":
        body = f"sibling edge to <code>{e(item['doc_b'])}</code> has no reverse"
        return f"<code>{e(item['doc_a'])}</code>", body, item["doc_a"]

    if rule == "stale_active_docs":
        last_mod = (item.get("last_modified") or "")[:10]
        return (
            f"<code>{e(item['path'])}</code>",
            f"last modified {e(last_mod)}",
            item["path"],
        )

    if rule == "inconsistent_hub_state":
        body = (
            f"downstream target <code>{e(item['member'])}</code>"
            f" is <code>{e(item['member_status'])}</code>"
        )
        return f"<code>{e(item['hub'])}</code> (active hub)", body, item["hub"]

    if rule == "orphan_docs":
        return f"<code>{e(item['path'])}</code>", "No inbound edges", item["path"]

    if rule == "decision_drift":
        title_text = item.get("title") or ""
        return (
            f"<code>{e(item['id'])}</code> ({e(item.get('date', '?'))})",
            f"{e(title_text)} &mdash; no ABOUT_DECISION edge",
            None,
        )

    if rule == "tag_drift":
        return f"<code>{e(item['tag'])}</code>", f"{item['doc_count']} doc(s)", None

    if rule == "decisions_format_drift":
        notes = item.get("notes") or "format drift detected"
        return f"<code>{e(item['id'])}</code>", e(notes), None

    return e(str(item)), "", None


# ---------------------------------------------------------------------------
# SVG corpus chart
# ---------------------------------------------------------------------------


_MIN_BAR_PX = 6


def _build_svg(status_counts: list[tuple[str, int]]) -> str:
    e = _html.escape
    max_count = max(c for _, c in status_counts)
    label_w = 85
    bar_area = 345
    count_w = 50
    total_w = label_w + bar_area + count_w
    row_h = 22
    pad = 5
    total_h = len(status_counts) * row_h + pad * 2

    mono = "font-family=\"'SF Mono', Menlo, Consolas, monospace\""
    lines = [
        f'<svg viewBox="0 0 {total_w} {total_h}" role="img" aria-label="Doc count per status">'
    ]
    for i, (status, count) in enumerate(status_counts):
        y = pad + i * row_h
        bar_w = max(_MIN_BAR_PX, int((count / max_count) * bar_area)) if max_count > 0 else 0
        color = _SVG_COLORS[i % len(_SVG_COLORS)]
        lines.append(
            f'  <text x="{label_w - 6}" y="{y + 14}" text-anchor="end"'
            f' {mono} font-size="11" fill="#8b949e">{e(status)}</text>'
        )
        if bar_w > 0:
            lines.append(
                f'  <rect x="{label_w}" y="{y + 4}" width="{bar_w}" height="13"'
                f' fill="{color}" rx="2"/>'
            )
        lines.append(
            f'  <text x="{label_w + bar_w + 5}" y="{y + 14}"'
            f' {mono} font-size="11" fill="#e1e4e8">{count}</text>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule_id(rule: str) -> str:
    return rule.replace("_", "-")
