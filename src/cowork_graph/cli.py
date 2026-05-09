"""CLI entry point."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from cowork_graph import config as cfg_mod
from cowork_graph import db, parser
from cowork_graph.walker import walk


_LABEL_TO_EDGE: dict[str, tuple[str, str]] = {
    "Related hubs": ("RELATED_TO", "parent_hub"),
    "Related hub": ("RELATED_TO", "parent_hub"),
    "Siblings": ("RELATED_TO", "sibling"),
    "Sibling": ("RELATED_TO", "sibling"),
    "Downstream": ("BLOCKS", "downstream"),
    "Upstream": ("BLOCKS", "upstream"),
}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "help":
        _print_help()
        return 0
    if args[0] == "build":
        return _cmd_build(args[1:])
    if args[0] == "update":
        return _cmd_update(args[1:])
    if args[0] == "reindex":
        return _cmd_reindex(args[1:])
    if args[0] == "mcp":
        return _cmd_mcp(args[1:])
    if args[0] == "audit":
        return _cmd_audit(args[1:])
    print(f"Unknown command: {args[0]}. Run 'cowork-graph help' for usage.", file=sys.stderr)
    return 1


def _print_help() -> None:
    print(
        "cowork-graph CLI\n"
        "\n"
        "Commands:\n"
        "  build                      Walk the cowork corpus and write the SQLite graph DB.\n"
        "  update --since <git-ref>   Incrementally re-parse files changed since <git-ref>.\n"
        "  reindex                    Full rebuild (alias for build; used by the git hook on merges).\n"
        "  audit [--write] [--html] [--no-html]\n"
        "                             Run ten drift-detection checks.\n"
        "                             --write: write report files to audits/ (markdown + HTML by default)\n"
        "                             --no-html: with --write, suppress HTML output (markdown only)\n"
        "                             --html: write HTML only (no markdown)\n"
        "  mcp serve                  Start the MCP server over stdio.\n"
        "  mcp install                Register with MCP clients (default: --all).\n"
        "  help                       Show this message.\n"
        "\n"
        "mcp install flags: --desktop  --code-wsl  --code-ps  --all\n"
        "\n"
        "Config: ~/.config/cowork-graph/config.toml (generated on first run)\n"
        "Env:    COWORK_GRAPH_DB_PATH overrides config.db_path\n"
        "\n"
        "See cowork/claude-environment/cowork-graph/plan.md for the schema and roadmap."
    )


def _cmd_mcp(args: list[str]) -> int:
    if not args or args[0] == "serve":
        from cowork_graph.mcp_server import main as mcp_main

        mcp_main()
        return 0
    if args[0] == "install":
        return _cmd_mcp_install(args[1:])
    print(f"Unknown mcp subcommand: {args[0]}. Use 'serve' or 'install'.", file=sys.stderr)
    return 1


def _cmd_mcp_install(args: list[str]) -> int:
    from cowork_graph.install import install, print_results

    flags = set(args)
    use_all = "--all" in flags or not flags
    results = install(
        desktop=use_all or "--desktop" in flags,
        code_wsl=use_all or "--code-wsl" in flags,
        code_ps=use_all or "--code-ps" in flags,
    )
    print_results(results)
    return 0


def _cmd_build(_args: list[str]) -> int:
    cfg = cfg_mod.load()
    cowork_root = cfg.cowork_root.resolve()

    if not cowork_root.exists():
        print(f"Error: cowork_root does not exist: {cowork_root}", file=sys.stderr)
        return 1

    db_path = cfg.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = db.connect(db_path)
    built_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    # ---------------------------------------------------------------------------
    # Pass 1: collect person nodes so the parser can match mentions
    # ---------------------------------------------------------------------------
    persons: dict[str, str] = {}  # slug -> display_name
    for abs_path, rel_path in walk(cowork_root):
        if not rel_path.startswith("memory/people/"):
            continue
        text = abs_path.read_text(encoding="utf-8")
        fm, _, _, _ = parser.parse_frontmatter(text)
        slug = abs_path.stem
        display_name = _display_name_from_fm(fm, slug)
        persons[slug] = display_name
        # Write person node immediately (known-good at this point)
        conn.execute("BEGIN")
        aliases = fm.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        db.upsert_person(
            conn,
            slug=slug,
            display_name=display_name,
            role=fm.get("role"),
            source_doc=rel_path,
            is_ghost=False,
        )
        for alias in aliases:
            db.upsert_person_alias(conn, slug=slug, alias=str(alias))
        conn.execute("COMMIT")

    # ---------------------------------------------------------------------------
    # Pass 2: walk and parse every doc
    # ---------------------------------------------------------------------------
    n_docs = 0
    n_failed = 0
    n_broken_links = 0
    # entity-vote accumulator for project→entity inference (second pass)
    project_entity_votes: dict[str, dict[str, int]] = {}  # project_slug -> {entity -> count}

    for abs_path, rel_path in walk(cowork_root):
        n_docs += 1
        text = abs_path.read_text(encoding="utf-8")
        try:
            last_mod = datetime.fromtimestamp(abs_path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            last_mod = None

        parsed_at = datetime.now(timezone.utc).isoformat()

        is_decision_log = abs_path.name == "decisions-log.md"

        _, body_text, _, _ = parser.parse_frontmatter(text)
        result = parser.parse_doc(
            path=rel_path,
            text=text,
            last_modified=last_mod,
            parsed_at=parsed_at,
            cowork_root=cowork_root,
            persons=persons,
        )
        if result.parse_status == "failed":
            n_failed += 1

        conn.execute("BEGIN")
        try:
            n_broken_links += _write_doc_to_db(
                conn,
                result,
                body_text,
                rel_path,
                parsed_at,
                is_decision_log,
                text,
                cowork_root,
                votes_acc=project_entity_votes,
            )
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            n_failed += 1
            print(f"Warning: failed to write {rel_path}: {exc}", file=sys.stderr)

    # ---------------------------------------------------------------------------
    # Pass 3: project→entity edges (majority-vote)
    # ---------------------------------------------------------------------------
    conn.execute("BEGIN")
    for slug, votes in project_entity_votes.items():
        winning_entity = max(votes, key=lambda e: votes[e])
        db.upsert_edge(
            conn,
            source_type="project",
            source_id=slug,
            edge_type="MEMBER_OF_ENTITY",
            target_type="entity",
            target_id=winning_entity,
        )
    conn.execute("COMMIT")

    # ---------------------------------------------------------------------------
    # Pass 4: resolve ghost projects (flip is_ghost=0 where hub doc exists)
    # ---------------------------------------------------------------------------
    conn.execute("BEGIN")
    db.resolve_ghost_projects(conn)
    conn.execute("COMMIT")

    # ---------------------------------------------------------------------------
    # Finalize meta and print summary
    # ---------------------------------------------------------------------------
    db.update_meta(conn, built_at=built_at, build_kind="full")
    conn.commit()
    conn.close()

    duration = time.monotonic() - t0
    print(f"{n_docs} docs, {n_failed} failed, {n_broken_links} broken links, {duration:.1f}s")

    fail_pct = (n_failed / n_docs * 100) if n_docs else 0
    if fail_pct > cfg.parser.fail_threshold_pct:
        print(
            f"Error: {fail_pct:.1f}% of docs failed parsing "
            f"(threshold: {cfg.parser.fail_threshold_pct}%)",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_audit(args: list[str]) -> int:
    import json
    from datetime import datetime, timezone
    from cowork_graph import audit as audit_mod

    write = "--write" in args
    no_html = "--no-html" in args
    html_flag = "--html" in args
    # --write alone now produces both markdown and HTML; --no-html opts out of HTML
    write_html = (write and not no_html) or html_flag
    cfg = cfg_mod.load()

    if not cfg.db_path.exists():
        print("Error: graph DB not found. Run 'cowork-graph build' first.", file=sys.stderr)
        return 1

    audits_dir = cfg.cowork_root / "claude-environment/cowork-graph/audits"
    conn = db.connect(cfg.db_path)
    try:
        result = audit_mod.run_audit(
            conn,
            write_report=write,
            report_dir=audits_dir if write else None,
            suppressions_path=cfg.suppressions_path,
        )
    finally:
        conn.close()

    if write_html:
        from cowork_graph.audit_html import write_html_report

        audits_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        html_path = audits_dir / f"{date_str}-audit.html"
        write_html_report(result, cfg.db_path, html_path, cowork_root=cfg.cowork_root)
        print(f"HTML report written: {html_path}")

    output: dict = {
        "total_findings": result["total_findings"],
        "suppressed": result["suppressed"],
        "summary": result["summary"],
    }
    print(json.dumps(output, indent=2))
    if result["report_written"]:
        print(f"Report written: {result['report_written']}")
    return 0


def _write_doc_to_db(
    conn,
    result,
    body_text: str,
    rel_path: str,
    parsed_at: str,
    is_decision_log: bool,
    text: str,
    cowork_root,
    *,
    votes_acc: dict | None,
) -> int:
    """Write all derived rows for one parsed doc. Caller holds the transaction.

    Returns the number of broken links recorded for this doc.
    """
    n_broken = 0

    db.upsert_doc(
        conn,
        path=result.path,
        title=result.title,
        status=result.status,
        doc_type=result.doc_type,
        word_count=result.word_count,
        link_count=result.link_count,
        last_modified=result.last_modified,
        parsed_at=result.parsed_at,
        parse_status=result.parse_status,
        parse_notes=result.parse_notes,
    )
    db.upsert_doc_fts(conn, path=result.path, title=result.title, body=body_text)

    for tag in result.tags:
        db.upsert_tag(conn, tag=tag)
        db.upsert_edge(
            conn,
            source_type="doc",
            source_id=rel_path,
            edge_type="TAGGED",
            target_type="tag",
            target_id=tag,
        )

    for slug in result.project_slugs:
        conn.execute(
            "INSERT OR IGNORE INTO project (slug, hub_doc, is_ghost) VALUES (?, NULL, 1)",
            (slug,),
        )
        db.upsert_edge(
            conn,
            source_type="doc",
            source_id=rel_path,
            edge_type="MEMBER_OF_PROJECT",
            target_type="project",
            target_id=slug,
        )
        if votes_acc is not None and result.entity:
            votes = votes_acc.setdefault(slug, {})
            votes[result.entity] = votes.get(result.entity, 0) + 1

    if result.entity:
        db.upsert_edge(
            conn,
            source_type="doc",
            source_id=rel_path,
            edge_type="MEMBER_OF_ENTITY",
            target_type="entity",
            target_id=result.entity,
        )

    for block in result.related_blocks:
        edge_type, edge_subtype = _LABEL_TO_EDGE.get(block.label, ("RELATED_TO", ""))
        for target in block.targets:
            # targets are root-relative normalized paths (normpath applied in parser.py)
            db.upsert_edge(
                conn,
                source_type="doc",
                source_id=rel_path,
                edge_type=edge_type,
                target_type="doc",
                target_id=target,
                edge_subtype=edge_subtype,
            )

    for link in result.links:
        if link.is_anchor or link.is_external:
            continue
        if link.is_broken:
            n_broken += 1
            db.upsert_broken_link(
                conn,
                source_doc=rel_path,
                link_text=link.text,
                link_target=link.raw_target,
                detected_at=parsed_at,
            )
        else:
            db.upsert_edge(
                conn,
                source_type="doc",
                source_id=rel_path,
                edge_type="LINKS_TO",
                target_type="doc",
                target_id=link.resolved or link.raw_target,
            )

    for mention in result.mentions:
        db.upsert_edge(
            conn,
            source_type="doc",
            source_id=rel_path,
            edge_type="MENTIONS",
            target_type="person",
            target_id=mention.person_slug,
            confidence="high",
            context=mention.context[:120],
        )

    if is_decision_log:
        decisions = parser.parse_decision_log(text, log_doc=rel_path, cowork_root=cowork_root)
        for decision in decisions:
            db.upsert_decision(
                conn,
                id=decision.id,
                date=decision.date,
                title=decision.title,
                decision_text=decision.decision_text,
                why=decision.why,
                alternatives=decision.alternatives,
                principle=decision.principle,
                source_context=decision.source_context,
                log_doc=rel_path,
                status=decision.status,
                parse_status=decision.parse_status,
                format_drift_notes=decision.format_drift_notes,
            )
            if decision.parse_status == "format_drift":
                db.upsert_format_drift(
                    conn,
                    artifact_kind="decision",
                    artifact_id=decision.id,
                    detected_at=parsed_at,
                    notes=decision.format_drift_notes or "",
                )
            for link in decision.about_links:
                db.upsert_edge(
                    conn,
                    source_type="decision",
                    source_id=decision.id,
                    edge_type="ABOUT_DECISION",
                    target_type="doc",
                    target_id=link.resolved,
                )

    return n_broken


def _cmd_update(args: list[str]) -> int:
    if "--since" not in args:
        print("Usage: cowork-graph update --since <git-ref>", file=sys.stderr)
        return 1
    idx = args.index("--since")
    if idx + 1 >= len(args):
        print("Error: --since requires a git ref argument.", file=sys.stderr)
        return 1
    since_ref = args[idx + 1]

    cfg = cfg_mod.load()
    cowork_root = cfg.cowork_root.resolve()

    if not cowork_root.exists():
        print(f"Error: cowork_root does not exist: {cowork_root}", file=sys.stderr)
        return 1

    if not cfg.db_path.exists():
        print("Graph DB not found — running full build.", file=sys.stderr)
        return _cmd_build([])

    from cowork_graph import incremental as inc_mod

    if inc_mod.is_merge_commit(cowork_root):
        print("Merge commit detected — running full rebuild.", file=sys.stderr)
        return _cmd_build([])

    conn = db.connect(cfg.db_path)
    t0 = time.monotonic()

    persons: dict[str, str] = {
        row["slug"]: row["display_name"]
        for row in conn.execute("SELECT slug, display_name FROM person WHERE is_ghost=0").fetchall()
    }

    try:
        summary = inc_mod.run_incremental(conn, since_ref, cowork_root, persons)
    finally:
        conn.close()

    duration = time.monotonic() - t0
    print(
        f"incremental: +{summary['added']} added, ~{summary['modified']} modified, "
        f"-{summary['deleted']} deleted, >{summary['renamed']} renamed, "
        f"{summary['failed']} failed, {duration:.1f}s"
    )
    return 0


def _cmd_reindex(args: list[str]) -> int:
    # Delete the DB so stale edges from previous index runs are eliminated.
    # INSERT OR REPLACE uses a PK that includes target_id, so normalized and
    # unnormalized forms of the same edge have different PKs and both survive
    # an additive rebuild. Wiping and rebuilding from scratch avoids this.
    cfg = cfg_mod.load()
    if cfg.db_path.exists():
        cfg.db_path.unlink()
    return _cmd_build([])


def _display_name_from_fm(fm: dict, slug: str) -> str:
    """Extract display_name from frontmatter or fall back to slug title-cased."""
    name = fm.get("display_name") or fm.get("name")
    if name:
        return str(name)
    return slug.replace("-", " ").title()


if __name__ == "__main__":
    sys.exit(main())
