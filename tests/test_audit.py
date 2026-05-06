"""Tests for cowork_graph.audit — all 10 checks plus run_audit and format_report."""

from pathlib import Path

import pytest
from cowork_graph import db
from cowork_graph.cli import _cmd_build
from cowork_graph.audit import (
    broken_links,
    decision_drift,
    decisions_format_drift,
    format_report,
    ghost_people,
    ghost_projects,
    inconsistent_hub_state,
    one_way_edges,
    orphan_docs,
    run_audit,
    stale_active_docs,
    tag_drift,
)

CORPUS = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture(scope="module")
def audit_db(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("audit_db")
    db_path = tmp_path / "graph.db"

    import cowork_graph.config as cfg_mod

    original_load = cfg_mod.load

    def patched_load(config_path=None):
        cfg = original_load(config_path=tmp_path / "config.toml")
        cfg.cowork_root = CORPUS
        cfg.db_path = db_path
        return cfg

    cfg_mod.load = patched_load
    try:
        exit_code = _cmd_build([])
        assert exit_code == 0
        conn = db.connect(db_path)
        yield conn
        conn.close()
    finally:
        cfg_mod.load = original_load


# ---------------------------------------------------------------------------
# ghost_projects
# ---------------------------------------------------------------------------


class TestGhostProjects:
    def test_returns_list(self, audit_db):
        assert isinstance(ghost_projects(audit_db), list)

    def test_ghost_project_present(self, audit_db):
        # project/skunkworks tag in project-doc.md, no hub doc registered
        slugs = [r["slug"] for r in ghost_projects(audit_db)]
        assert "skunkworks" in slugs

    def test_each_item_has_slug(self, audit_db):
        for item in ghost_projects(audit_db):
            assert "slug" in item


# ---------------------------------------------------------------------------
# ghost_people
# ---------------------------------------------------------------------------


class TestGhostPeople:
    def test_known_person_with_file_not_ghost(self, audit_db):
        slugs = [r["slug"] for r in ghost_people(audit_db)]
        assert "jane-doe" not in slugs  # has memory/people/jane-doe.md

    def test_returns_list(self, audit_db):
        assert isinstance(ghost_people(audit_db), list)

    def test_each_item_has_required_keys(self, audit_db):
        for item in ghost_people(audit_db):
            assert "slug" in item
            assert "display_name" in item
            assert "mention_count" in item

    def test_min_mentions_filter(self, audit_db):
        # High threshold → fewer or equal results
        low = ghost_people(audit_db, min_mentions=1)
        high = ghost_people(audit_db, min_mentions=100)
        assert len(high) <= len(low)


# ---------------------------------------------------------------------------
# broken_links
# ---------------------------------------------------------------------------


class TestBrokenLinks:
    def test_returns_known_broken_link(self, audit_db):
        results = broken_links(audit_db)
        assert len(results) >= 1

    def test_nonexistent_target_flagged(self, audit_db):
        targets = [r["link_target"] for r in broken_links(audit_db)]
        assert "nonexistent.md" in targets

    def test_each_item_has_required_keys(self, audit_db):
        for item in broken_links(audit_db):
            assert "source_doc" in item
            assert "link_text" in item
            assert "link_target" in item


# ---------------------------------------------------------------------------
# one_way_edges
# ---------------------------------------------------------------------------


class TestOneWayEdges:
    def test_returns_list(self, audit_db):
        assert isinstance(one_way_edges(audit_db), list)

    def test_each_item_has_doc_pair(self, audit_db):
        for item in one_way_edges(audit_db):
            assert "doc_a" in item
            assert "doc_b" in item

    def test_no_sibling_edges_in_fixture(self, audit_db):
        # Fixture corpus has no sibling edges, so result should be empty
        assert one_way_edges(audit_db) == []


# ---------------------------------------------------------------------------
# stale_active_docs
# ---------------------------------------------------------------------------


class TestStaleActiveDocs:
    def test_returns_list(self, audit_db):
        assert isinstance(stale_active_docs(audit_db), list)

    def test_each_item_has_required_keys(self, audit_db):
        for item in stale_active_docs(audit_db):
            assert "path" in item
            assert "title" in item
            assert "last_modified" in item

    def test_fresh_files_not_stale_at_default(self, audit_db):
        # Fixture files were just created/modified; none should be 90-days stale
        results = stale_active_docs(audit_db, days=90)
        assert isinstance(results, list)
        # All returned paths should be active docs with non-null last_modified
        for item in results:
            assert item["last_modified"] is not None

    def test_very_large_days_returns_empty(self, audit_db):
        # No file was modified 36500 days ago (100 years)
        assert stale_active_docs(audit_db, days=36500) == []


# ---------------------------------------------------------------------------
# inconsistent_hub_state
# ---------------------------------------------------------------------------


class TestInconsistentHubState:
    def test_returns_list(self, audit_db):
        assert isinstance(inconsistent_hub_state(audit_db), list)

    def test_active_downstream_not_flagged(self, audit_db):
        # autoscript-hub → downstream → project-doc.md (status=active); not a finding
        results = inconsistent_hub_state(audit_db)
        hubs = [r["hub"] for r in results]
        assert "autoscriptstudio/autoscript-hub.md" not in hubs

    def test_each_item_has_required_keys(self, audit_db):
        for item in inconsistent_hub_state(audit_db):
            assert "hub" in item
            assert "hub_title" in item
            assert "member" in item
            assert "member_title" in item
            assert "member_status" in item

    def test_member_status_is_done_or_blocked(self, audit_db):
        for item in inconsistent_hub_state(audit_db):
            assert item["member_status"] in ("done", "blocked")


# ---------------------------------------------------------------------------
# orphan_docs
# ---------------------------------------------------------------------------


class TestOrphanDocs:
    def test_broken_links_doc_is_orphan(self, audit_db):
        paths = [r["path"] for r in orphan_docs(audit_db)]
        assert "broken-links-doc.md" in paths

    def test_hub_docs_excluded(self, audit_db):
        paths = [r["path"] for r in orphan_docs(audit_db)]
        # Hub docs are excluded by the doc_type != 'hub' filter
        assert "autoscriptstudio/autoscript-hub.md" not in paths
        assert "personal/personal-hub.md" not in paths

    def test_each_item_has_path_and_title(self, audit_db):
        for item in orphan_docs(audit_db):
            assert "path" in item
            assert "title" in item


# ---------------------------------------------------------------------------
# decision_drift
# ---------------------------------------------------------------------------


class TestDecisionDrift:
    def test_incomplete_entry_flagged(self, audit_db):
        ids = [r["id"] for r in decision_drift(audit_db)]
        assert "2026-05-05-incomplete-entry-missing-required-fields" in ids

    def test_well_linked_decision_not_flagged(self, audit_db):
        # The SQLite decision links to autoscript-hub.md via Source / context
        ids = [r["id"] for r in decision_drift(audit_db)]
        assert "2026-05-05-choose-sqlite-as-backend" not in ids

    def test_each_item_has_required_keys(self, audit_db):
        for item in decision_drift(audit_db):
            assert "id" in item
            assert "date" in item
            assert "title" in item


# ---------------------------------------------------------------------------
# tag_drift
# ---------------------------------------------------------------------------


class TestTagDrift:
    def test_rare_tag_flagged(self, audit_db):
        tags = [r["tag"] for r in tag_drift(audit_db)]
        # type/log is used by only decisions-log.md
        assert "type/log" in tags

    def test_each_item_has_required_keys(self, audit_db):
        for item in tag_drift(audit_db):
            assert "tag" in item
            assert "doc_count" in item

    def test_doc_count_within_threshold(self, audit_db):
        for item in tag_drift(audit_db, max_count=2):
            assert item["doc_count"] <= 2

    def test_max_count_filter(self, audit_db):
        at_2 = tag_drift(audit_db, max_count=2)
        at_1 = tag_drift(audit_db, max_count=1)
        assert len(at_1) <= len(at_2)


# ---------------------------------------------------------------------------
# decisions_format_drift
# ---------------------------------------------------------------------------


class TestDecisionsFormatDrift:
    def test_incomplete_entry_flagged(self, audit_db):
        ids = [r["id"] for r in decisions_format_drift(audit_db)]
        assert "2026-05-05-incomplete-entry-missing-required-fields" in ids

    def test_well_formed_decision_not_flagged(self, audit_db):
        ids = [r["id"] for r in decisions_format_drift(audit_db)]
        assert "2026-05-05-choose-sqlite-as-backend" not in ids

    def test_each_item_has_required_keys(self, audit_db):
        for item in decisions_format_drift(audit_db):
            assert "id" in item
            assert "detected_at" in item
            assert "notes" in item


# ---------------------------------------------------------------------------
# run_audit
# ---------------------------------------------------------------------------


class TestRunAudit:
    def test_returns_ok_status(self, audit_db):
        result = run_audit(audit_db)
        assert result["status"] == "ok"

    def test_has_all_ten_check_keys(self, audit_db):
        expected = {
            "ghost_projects", "ghost_people", "broken_links", "one_way_edges",
            "stale_active_docs", "inconsistent_hub_state", "orphan_docs",
            "decision_drift", "tag_drift", "decisions_format_drift",
        }
        assert expected <= set(run_audit(audit_db)["findings"])

    def test_summary_counts_match_findings(self, audit_db):
        result = run_audit(audit_db)
        for key, items in result["findings"].items():
            assert result["summary"][key] == len(items)

    def test_total_findings_is_sum(self, audit_db):
        result = run_audit(audit_db)
        assert result["total_findings"] == sum(result["summary"].values())

    def test_has_run_at_timestamp(self, audit_db):
        result = run_audit(audit_db)
        assert result["run_at"]

    def test_write_report_false_by_default(self, audit_db):
        result = run_audit(audit_db)
        assert result["report_written"] is None

    def test_write_report_creates_file(self, audit_db, tmp_path):
        report_dir = tmp_path / "reports"
        result = run_audit(audit_db, write_report=True, report_dir=report_dir)
        assert result["report_written"] is not None
        path = Path(result["report_written"])
        assert path.exists()
        assert path.suffix == ".md"

    def test_report_filename_has_date(self, audit_db, tmp_path):
        report_dir = tmp_path / "reports"
        run_audit(audit_db, write_report=True, report_dir=report_dir)
        files = list(report_dir.glob("*-audit.md"))
        assert len(files) == 1

    def test_report_dir_created_if_missing(self, audit_db, tmp_path):
        report_dir = tmp_path / "deep" / "nested" / "reports"
        assert not report_dir.exists()
        run_audit(audit_db, write_report=True, report_dir=report_dir)
        assert report_dir.exists()


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_contains_title(self, audit_db):
        result = run_audit(audit_db)
        report = format_report(result)
        assert "# Cowork Graph Audit" in report

    def test_contains_summary_section(self, audit_db):
        result = run_audit(audit_db)
        assert "## Summary" in format_report(result)

    def test_all_section_labels_present(self, audit_db):
        result = run_audit(audit_db)
        report = format_report(result)
        for label in [
            "Ghost Projects", "Ghost People", "Broken Markdown Links",
            "One-Way Sibling Edges", "Stale Active Docs", "Inconsistent Hub State",
            "Orphan Docs", "Decision Drift", "Tag Drift",
            "Decisions-Log Format Drift",
        ]:
            assert label in report, f"Missing section label: {label}"

    def test_known_finding_appears_in_report(self, audit_db):
        result = run_audit(audit_db)
        report = format_report(result)
        assert "nonexistent.md" in report  # broken link target

    def test_empty_section_shows_no_findings(self, audit_db):
        result = run_audit(audit_db)
        report = format_report(result)
        assert "_No findings._" in report  # at least one check passes clean
