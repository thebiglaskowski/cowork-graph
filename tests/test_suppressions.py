"""Tests for cowork_graph.suppressions — key derivation, load, filter."""

import pytest

from cowork_graph.suppressions import (
    _make_key,
    filter_findings,
    is_suppressed,
    load_suppressions,
)


# ---------------------------------------------------------------------------
# _make_key — one test per rule
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_broken_links(self):
        finding = {"source_doc": "CLAUDE.md", "link_text": "x", "link_target": "relative-path.md"}
        assert _make_key("broken_links", finding) == "CLAUDE.md::relative-path.md"

    def test_ghost_projects(self):
        finding = {"slug": "seoforge"}
        assert _make_key("ghost_projects", finding) == "seoforge"

    def test_ghost_people(self):
        finding = {"slug": "jane-doe", "display_name": "Jane Doe", "mention_count": 3}
        assert _make_key("ghost_people", finding) == "jane-doe"

    def test_one_way_edges(self):
        finding = {"doc_a": "personal/foo.md", "doc_b": "personal/bar.md"}
        assert _make_key("one_way_edges", finding) == "personal/foo.md::personal/bar.md"

    def test_stale_active_docs(self):
        finding = {"path": "autoscriptstudio/hub.md", "title": "Hub", "last_modified": "2025-01-01"}
        assert _make_key("stale_active_docs", finding) == "autoscriptstudio/hub.md"

    def test_inconsistent_hub_state(self):
        finding = {
            "hub": "autoscriptstudio/hub.md",
            "hub_title": "Hub",
            "member": "autoscriptstudio/project.md",
            "member_title": "Project",
            "member_status": "done",
        }
        assert _make_key("inconsistent_hub_state", finding) == (
            "autoscriptstudio/hub.md::autoscriptstudio/project.md"
        )

    def test_orphan_docs(self):
        finding = {"path": "personal/orphan.md", "title": "Orphan"}
        assert _make_key("orphan_docs", finding) == "personal/orphan.md"

    def test_decision_drift(self):
        finding = {"id": "2026-05-05-choose-sqlite", "date": "2026-05-05", "title": "Choose SQLite"}
        assert _make_key("decision_drift", finding) == "2026-05-05-choose-sqlite"

    def test_tag_drift(self):
        finding = {"tag": "type/log", "doc_count": 1}
        assert _make_key("tag_drift", finding) == "type/log"

    def test_decisions_format_drift(self):
        finding = {"id": "2026-05-05-bad-entry", "detected_at": "...", "notes": "missing fields"}
        assert _make_key("decisions_format_drift", finding) == "2026-05-05-bad-entry"

    def test_unknown_rule_raises(self):
        with pytest.raises(ValueError, match="Unknown rule"):
            _make_key("nonexistent_rule", {})


# ---------------------------------------------------------------------------
# load_suppressions
# ---------------------------------------------------------------------------


class TestLoadSuppressions:
    def test_valid_frontmatter_returns_tuple_set(self, tmp_path):
        f = tmp_path / "suppressions.md"
        f.write_text(
            "---\n"
            "suppressions:\n"
            "  - rule: broken_links\n"
            "    key: CLAUDE.md::relative-path.md\n"
            "    reason: Template literal\n"
            "    added: 2026-05-06\n"
            "  - rule: ghost_projects\n"
            "    key: seoforge\n"
            "    reason: Deferred\n"
            "    added: 2026-05-06\n"
            "---\n\n# Audit Suppressions\n",
            encoding="utf-8",
        )
        result = load_suppressions(f)
        assert ("broken_links", "CLAUDE.md::relative-path.md") in result
        assert ("ghost_projects", "seoforge") in result
        assert len(result) == 2

    def test_missing_file_autocreates_template(self, tmp_path):
        f = tmp_path / "subdir" / "suppressions.md"
        assert not f.exists()
        result = load_suppressions(f)
        assert result == set()
        assert f.exists()
        content = f.read_text(encoding="utf-8")
        assert "suppressions: []" in content

    def test_missing_file_template_has_body(self, tmp_path):
        f = tmp_path / "suppressions.md"
        load_suppressions(f)
        content = f.read_text(encoding="utf-8")
        assert "# Audit Suppressions" in content

    def test_empty_suppressions_list_returns_empty_set(self, tmp_path):
        f = tmp_path / "suppressions.md"
        f.write_text("---\nsuppressions: []\n---\n\n# Audit Suppressions\n", encoding="utf-8")
        assert load_suppressions(f) == set()

    def test_malformed_yaml_returns_empty_set(self, tmp_path):
        f = tmp_path / "suppressions.md"
        f.write_text("---\nsuppressions: [unclosed\n---\n", encoding="utf-8")
        result = load_suppressions(f)
        assert result == set()

    def test_missing_suppressions_field_returns_empty_set(self, tmp_path):
        f = tmp_path / "suppressions.md"
        f.write_text("---\ntags:\n  - status/active\n---\n", encoding="utf-8")
        assert load_suppressions(f) == set()

    def test_entries_without_rule_or_key_skipped(self, tmp_path):
        f = tmp_path / "suppressions.md"
        f.write_text(
            "---\n"
            "suppressions:\n"
            "  - rule: broken_links\n"
            "    reason: no key field\n"
            "  - key: seoforge\n"
            "    reason: no rule field\n"
            "---\n",
            encoding="utf-8",
        )
        assert load_suppressions(f) == set()


# ---------------------------------------------------------------------------
# is_suppressed
# ---------------------------------------------------------------------------


class TestIsSuppressed:
    def test_matching_tuple_returns_true(self):
        suppressions = {("broken_links", "CLAUDE.md::relative-path.md")}
        finding = {"source_doc": "CLAUDE.md", "link_text": "x", "link_target": "relative-path.md"}
        assert is_suppressed("broken_links", finding, suppressions) is True

    def test_non_matching_tuple_returns_false(self):
        suppressions = {("broken_links", "other.md::target.md")}
        finding = {"source_doc": "CLAUDE.md", "link_text": "x", "link_target": "relative-path.md"}
        assert is_suppressed("broken_links", finding, suppressions) is False

    def test_wrong_rule_returns_false(self):
        suppressions = {("ghost_projects", "CLAUDE.md::relative-path.md")}
        finding = {"source_doc": "CLAUDE.md", "link_text": "x", "link_target": "relative-path.md"}
        assert is_suppressed("broken_links", finding, suppressions) is False

    def test_empty_suppressions_returns_false(self):
        finding = {"slug": "seoforge"}
        assert is_suppressed("ghost_projects", finding, set()) is False


# ---------------------------------------------------------------------------
# filter_findings
# ---------------------------------------------------------------------------


class TestFilterFindings:
    def test_suppressed_finding_removed_from_list(self):
        suppressions = {("ghost_projects", "seoforge")}
        findings = [{"slug": "seoforge"}, {"slug": "other-project"}]
        unsuppressed, count = filter_findings("ghost_projects", findings, suppressions)
        assert count == 1
        assert len(unsuppressed) == 1
        assert unsuppressed[0]["slug"] == "other-project"

    def test_all_unsuppressed_returns_full_list(self):
        findings = [{"slug": "alpha"}, {"slug": "beta"}]
        unsuppressed, count = filter_findings("ghost_projects", findings, set())
        assert count == 0
        assert unsuppressed == findings

    def test_all_suppressed_returns_empty_list(self):
        suppressions = {("ghost_projects", "alpha"), ("ghost_projects", "beta")}
        findings = [{"slug": "alpha"}, {"slug": "beta"}]
        unsuppressed, count = filter_findings("ghost_projects", findings, suppressions)
        assert count == 2
        assert unsuppressed == []

    def test_empty_findings_returns_zero(self):
        unsuppressed, count = filter_findings("ghost_projects", [], {("ghost_projects", "x")})
        assert count == 0
        assert unsuppressed == []

    def test_mixed_partition(self):
        suppressions = {
            ("broken_links", "doc-a.md::missing.md"),
            ("broken_links", "doc-b.md::also-missing.md"),
        }
        findings = [
            {"source_doc": "doc-a.md", "link_text": "a", "link_target": "missing.md"},
            {"source_doc": "doc-c.md", "link_text": "c", "link_target": "other.md"},
            {"source_doc": "doc-b.md", "link_text": "b", "link_target": "also-missing.md"},
        ]
        unsuppressed, count = filter_findings("broken_links", findings, suppressions)
        assert count == 2
        assert len(unsuppressed) == 1
        assert unsuppressed[0]["source_doc"] == "doc-c.md"
