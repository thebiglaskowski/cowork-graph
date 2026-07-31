"""Tests for queries.py — all functions exercised against the fixture corpus DB."""

from pathlib import Path

import pytest
from cowork_graph import db
from cowork_graph.cli import _cmd_build
from cowork_graph.queries import (
    audit,
    count_active,
    count_blocked,
    count_decisions,
    decisions,
    get_doc,
    list_active,
    list_blocked,
    project_state,
    search_docs,
    who,
)

CORPUS = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture(scope="module")
def graph_db(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("queries_db")
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


class TestSearchDocs:
    def test_returns_results_for_matching_query(self, graph_db):
        hits = search_docs(graph_db, "Jane")
        assert len(hits) > 0

    def test_hit_has_required_fields(self, graph_db):
        hits = search_docs(graph_db, "Jane")
        h = hits[0]
        assert h.path
        assert isinstance(h.snippet, str)
        assert isinstance(h.score, float)

    def test_status_filter_respected(self, graph_db):
        hits = search_docs(graph_db, "Jane", status="active")
        assert all(h.path for h in hits)  # just verify no crash
        # All returned docs should be active
        for hit in hits:
            row = graph_db.execute(
                "SELECT status FROM doc WHERE path=?", (hit.path,)
            ).fetchone()
            assert row["status"] == "active"

    def test_scope_filter_restricts_to_prefix(self, graph_db):
        hits = search_docs(graph_db, "Jane", scope="autoscriptstudio")
        assert all(h.path.startswith("autoscriptstudio/") for h in hits)

    def test_tag_filter_respected(self, graph_db):
        hits = search_docs(graph_db, "Jane", tags=["type/hub"])
        for hit in hits:
            row = graph_db.execute(
                "SELECT 1 FROM edge WHERE source_type='doc' AND source_id=?"
                " AND edge_type='TAGGED' AND target_id='type/hub'",
                (hit.path,),
            ).fetchone()
            assert row is not None

    def test_no_results_for_nonsense_query(self, graph_db):
        hits = search_docs(graph_db, "xyzzy_nonexistent_token_42")
        assert hits == []


class TestGetDoc:
    def test_returns_doc_detail(self, graph_db):
        detail = get_doc(graph_db, "autoscriptstudio/autoscript-hub.md")
        assert detail is not None
        assert detail.path == "autoscriptstudio/autoscript-hub.md"
        assert detail.title is not None
        assert detail.status == "active"

    def test_has_outbound_edges(self, graph_db):
        detail = get_doc(graph_db, "autoscriptstudio/autoscript-hub.md")
        assert detail is not None
        edge_types = {e["edge_type"] for e in detail.outbound}
        assert "TAGGED" in edge_types

    def test_has_inbound_edges(self, graph_db):
        # personal-hub.md is linked from autoscript-hub via RELATED_TO
        detail = get_doc(graph_db, "personal/personal-hub.md")
        assert detail is not None
        assert len(detail.inbound) > 0

    def test_returns_none_for_missing_path(self, graph_db):
        detail = get_doc(graph_db, "does/not/exist.md")
        assert detail is None


class TestListActive:
    def test_returns_active_docs(self, graph_db):
        docs = list_active(graph_db)
        assert len(docs) > 0
        assert all(d.status == "active" for d in docs)

    def test_scope_filter(self, graph_db):
        docs = list_active(graph_db, scope="autoscriptstudio")
        assert all(d.path.startswith("autoscriptstudio/") for d in docs)
        assert len(docs) >= 1

    def test_project_filter(self, graph_db):
        docs = list_active(graph_db, project="skunkworks")
        assert len(docs) >= 1
        # All returned docs must be members of skunkworks
        for doc in docs:
            row = graph_db.execute(
                "SELECT 1 FROM edge WHERE source_type='doc' AND source_id=?"
                " AND edge_type='MEMBER_OF_PROJECT' AND target_id='skunkworks'",
                (doc.path,),
            ).fetchone()
            assert row is not None

    def test_empty_project_returns_empty(self, graph_db):
        docs = list_active(graph_db, project="nonexistent-project-zzz")
        assert docs == []


class TestListBlocked:
    def test_returns_list(self, graph_db):
        # Fixture corpus has no blocked docs — verify function returns empty list cleanly
        result = list_blocked(graph_db)
        assert isinstance(result, list)

    def test_blocked_doc_has_blockers_field(self, graph_db):
        result = list_blocked(graph_db)
        for doc in result:
            assert isinstance(doc.blockers, list)


class TestProjectState:
    def test_returns_project_for_known_slug(self, graph_db):
        state = project_state(graph_db, "skunkworks")
        assert state is not None
        assert state.slug == "skunkworks"

    def test_member_docs_populated(self, graph_db):
        state = project_state(graph_db, "skunkworks")
        assert state is not None
        assert len(state.member_docs) >= 1
        paths = [d.path for d in state.member_docs]
        assert "personal/project-doc.md" in paths

    def test_status_mix_populated(self, graph_db):
        state = project_state(graph_db, "skunkworks")
        assert state is not None
        assert isinstance(state.status_mix, dict)
        total = sum(state.status_mix.values())
        assert total == len(state.member_docs)

    def test_returns_none_for_unknown_slug(self, graph_db):
        state = project_state(graph_db, "nonexistent-slug-zzz")
        assert state is None


class TestWho:
    def test_finds_known_person(self, graph_db):
        profile = who(graph_db, "Jane Doe")
        assert profile is not None
        assert profile.slug == "jane-doe"
        assert profile.display_name == "Jane Doe"

    def test_case_insensitive_name_match(self, graph_db):
        profile = who(graph_db, "jane doe")
        assert profile is not None
        assert profile.slug == "jane-doe"

    def test_mentions_populated(self, graph_db):
        profile = who(graph_db, "Jane Doe")
        assert profile is not None
        assert len(profile.mentions) >= 1

    def test_returns_none_for_unknown_person(self, graph_db):
        profile = who(graph_db, "Nobody Knowsbody")
        assert profile is None


class TestDecisions:
    def test_returns_all_decisions(self, graph_db):
        entries = decisions(graph_db)
        assert len(entries) >= 1

    def test_decision_has_required_fields(self, graph_db):
        entries = decisions(graph_db)
        d = entries[0]
        assert d.id
        assert d.date
        assert d.title

    def test_since_filter(self, graph_db):
        entries = decisions(graph_db, since="2026-01-01")
        assert all(e.date >= "2026-01-01" for e in entries)

    def test_topic_filter(self, graph_db):
        entries = decisions(graph_db, topic="SQLite")
        assert len(entries) >= 1
        assert any("SQLite" in e.title for e in entries)

    def test_about_docs_is_list(self, graph_db):
        entries = decisions(graph_db)
        for e in entries:
            assert isinstance(e.about_docs, list)

    def test_since_future_returns_empty(self, graph_db):
        entries = decisions(graph_db, since="2099-01-01")
        assert entries == []


class TestAudit:
    def test_returns_ok(self, graph_db):
        result = audit(graph_db)
        assert result["status"] == "ok"
        assert result["total_findings"] >= 0


class TestLimitsAndCounts:
    """limit params cap result size; count_* report the unfiltered total."""

    def test_count_active_matches_unlimited_list(self, graph_db):
        assert count_active(graph_db) == len(list_active(graph_db))

    def test_count_active_respects_filters(self, graph_db):
        n = count_active(graph_db, scope="autoscriptstudio")
        assert n == len(list_active(graph_db, scope="autoscriptstudio"))
        assert n < count_active(graph_db)

    def test_list_active_limit_caps_results(self, graph_db):
        assert count_active(graph_db) > 1  # limit must actually bite
        docs = list_active(graph_db, limit=1)
        assert len(docs) == 1

    def test_list_active_limit_keeps_newest_first(self, graph_db):
        all_docs = list_active(graph_db)
        limited = list_active(graph_db, limit=2)
        assert [d.path for d in limited] == [d.path for d in all_docs[:2]]
        stamps = [d.last_modified for d in all_docs if d.last_modified]
        assert stamps == sorted(stamps, reverse=True)

    def test_negative_limit_returns_empty(self, graph_db):
        assert list_active(graph_db, limit=-5) == []

    def test_count_blocked_matches_list(self, graph_db):
        assert count_blocked(graph_db) == len(list_blocked(graph_db))

    def test_list_blocked_accepts_limit(self, graph_db):
        assert len(list_blocked(graph_db, limit=1)) <= 1

    def test_count_decisions_matches_unlimited_list(self, graph_db):
        assert count_decisions(graph_db) == len(decisions(graph_db))

    def test_count_decisions_respects_filters(self, graph_db):
        n = count_decisions(graph_db, topic="SQLite")
        assert n == len(decisions(graph_db, topic="SQLite"))
        assert count_decisions(graph_db, since="2099-01-01") == 0

    def test_decisions_limit_caps_results(self, graph_db):
        assert len(decisions(graph_db, limit=1)) == 1

    def test_who_mentions_total_matches_unlimited(self, graph_db):
        profile = who(graph_db, "Jane Doe")
        assert profile.mentions_total == len(profile.mentions)

    def test_who_mentions_limit_caps_but_total_stays_full(self, graph_db):
        full = who(graph_db, "Jane Doe")
        limited = who(graph_db, "Jane Doe", mentions_limit=0)
        assert limited.mentions == []
        assert limited.mentions_total == full.mentions_total > 0
