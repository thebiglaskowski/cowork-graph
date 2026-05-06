"""Tests for incremental.py — pure unit tests for parsers, integration tests with real git."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from cowork_graph import db
from cowork_graph.incremental import (
    DiffResult,
    _parse_name_status,
    _recompute_project_entities,
    _should_skip,
    run_incremental,
)


# ---------------------------------------------------------------------------
# _parse_name_status — pure unit tests (no subprocess)
# ---------------------------------------------------------------------------

class TestParseNameStatus:
    def test_added(self):
        result = _parse_name_status("A\tpersonal/new.md\n")
        assert result.added == ["personal/new.md"]
        assert result.modified == []

    def test_modified(self):
        result = _parse_name_status("M\tautoscriptstudio/hub.md\n")
        assert result.modified == ["autoscriptstudio/hub.md"]

    def test_deleted(self):
        result = _parse_name_status("D\tpersonal/old.md\n")
        assert result.deleted == ["personal/old.md"]

    def test_renamed_exact(self):
        result = _parse_name_status("R100\tpersonal/old.md\tpersonal/new.md\n")
        assert result.renamed == [("personal/old.md", "personal/new.md")]

    def test_renamed_partial_similarity(self):
        result = _parse_name_status("R80\tautoscriptstudio/old.md\tautoscriptstudio/new.md\n")
        assert result.renamed == [("autoscriptstudio/old.md", "autoscriptstudio/new.md")]

    def test_multiple_lines(self):
        output = "A\tfile-a.md\nM\tfile-b.md\nD\tfile-c.md\n"
        result = _parse_name_status(output)
        assert result.added == ["file-a.md"]
        assert result.modified == ["file-b.md"]
        assert result.deleted == ["file-c.md"]

    def test_blank_lines_ignored(self):
        result = _parse_name_status("\nA\tpersonal/new.md\n\n")
        assert result.added == ["personal/new.md"]

    def test_empty_output(self):
        result = _parse_name_status("")
        assert result == DiffResult()


# ---------------------------------------------------------------------------
# _should_skip — pure unit tests
# ---------------------------------------------------------------------------

class TestShouldSkip:
    def test_markdown_not_skipped(self):
        assert _should_skip("personal/hub.md") is False

    def test_non_markdown_skipped(self):
        assert _should_skip("personal/image.png") is True

    def test_archive_skipped(self):
        assert _should_skip("autoscriptstudio/_archive/old.md") is True

    def test_obsidian_skipped(self):
        assert _should_skip(".obsidian/config.json") is True

    def test_nested_archive_skipped(self):
        assert _should_skip("personal/projects/_archive/done.md") is True


# ---------------------------------------------------------------------------
# _recompute_project_entities — in-memory DB
# ---------------------------------------------------------------------------

class TestRecomputeProjectEntities:
    def test_picks_majority_entity(self):
        conn = db.connect(":memory:")
        conn.execute("BEGIN")
        # Two docs in project 'alpha', both in entity 'autoscriptstudio'
        for i in range(2):
            conn.execute(
                "INSERT OR IGNORE INTO doc (path, parsed_at, parse_status) VALUES (?, ?, ?)",
                (f"autoscriptstudio/doc{i}.md", "2026-05-05T00:00:00", "ok"),
            )
            db.upsert_edge(conn, source_type="doc", source_id=f"autoscriptstudio/doc{i}.md",
                           edge_type="MEMBER_OF_PROJECT", target_type="project", target_id="alpha")
            db.upsert_edge(conn, source_type="doc", source_id=f"autoscriptstudio/doc{i}.md",
                           edge_type="MEMBER_OF_ENTITY", target_type="entity", target_id="autoscriptstudio")
        # One doc in project 'alpha' from entity 'personal'
        conn.execute(
            "INSERT OR IGNORE INTO doc (path, parsed_at, parse_status) VALUES (?, ?, ?)",
            ("personal/note.md", "2026-05-05T00:00:00", "ok"),
        )
        db.upsert_edge(conn, source_type="doc", source_id="personal/note.md",
                       edge_type="MEMBER_OF_PROJECT", target_type="project", target_id="alpha")
        db.upsert_edge(conn, source_type="doc", source_id="personal/note.md",
                       edge_type="MEMBER_OF_ENTITY", target_type="entity", target_id="personal")
        conn.execute("INSERT OR IGNORE INTO project (slug, hub_doc, is_ghost) VALUES ('alpha', NULL, 1)")
        conn.execute("COMMIT")

        conn.execute("BEGIN")
        _recompute_project_entities(conn)
        conn.execute("COMMIT")

        row = conn.execute(
            "SELECT target_id FROM edge WHERE source_type='project' AND source_id='alpha'"
            " AND edge_type='MEMBER_OF_ENTITY'"
        ).fetchone()
        assert row is not None
        assert row[0] == "autoscriptstudio"
        conn.close()

    def test_no_project_edges_leaves_no_rows(self):
        conn = db.connect(":memory:")
        conn.execute("BEGIN")
        _recompute_project_entities(conn)
        conn.execute("COMMIT")
        count = conn.execute(
            "SELECT COUNT(*) FROM edge WHERE source_type='project' AND edge_type='MEMBER_OF_ENTITY'"
        ).fetchone()[0]
        assert count == 0
        conn.close()


# ---------------------------------------------------------------------------
# run_incremental — integration tests using a real git repo in tmp_path
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(
        ["git"] + args, cwd=cwd, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _init_git_repo(path: Path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit_all(path: Path, message: str) -> str:
    _git(["add", "-A"], path)
    _git(["commit", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path)


@pytest.fixture
def corpus(tmp_path):
    """Minimal git-backed corpus with one initial commit."""
    root = tmp_path / "cowork"
    root.mkdir()
    _init_git_repo(root)

    # Initial commit: one doc in autoscriptstudio entity
    (root / "autoscriptstudio").mkdir()
    (root / "autoscriptstudio" / "hub.md").write_text(
        dedent("""\
            ---
            title: Hub
            status: active
            doc_type: hub
            entity: autoscriptstudio
            ---
            # Hub
            Some content.
        """)
    )
    _commit_all(root, "initial")
    return root


@pytest.fixture
def incremental_db(corpus, tmp_path):
    """Full-build DB from the initial corpus commit."""
    from cowork_graph.cli import _cmd_build
    import cowork_graph.config as cfg_mod

    db_path = tmp_path / "graph.db"
    original_load = cfg_mod.load

    def patched_load(config_path=None):
        cfg = original_load(config_path=tmp_path / "config.toml")
        cfg.cowork_root = corpus
        cfg.db_path = db_path
        return cfg

    cfg_mod.load = patched_load
    try:
        assert _cmd_build([]) == 0
    finally:
        cfg_mod.load = original_load

    return db_path, corpus


class TestRunIncrementalAddDoc:
    def test_new_doc_appears_in_db(self, incremental_db):
        db_path, corpus = incremental_db
        new_doc = corpus / "personal" / "note.md"
        new_doc.parent.mkdir(exist_ok=True)
        new_doc.write_text(
            dedent("""\
                ---
                title: My Note
                status: active
                doc_type: note
                entity: personal
                ---
                # My Note
                Hello world.
            """)
        )
        _commit_all(corpus, "add note")

        conn = db.connect(db_path)
        persons: dict = {}
        try:
            run_incremental(conn, "HEAD~1", corpus, persons)
        finally:
            conn.close()

        conn = db.connect(db_path)
        row = conn.execute("SELECT title FROM doc WHERE path='personal/note.md'").fetchone()
        conn.close()
        assert row is not None
        assert row["title"] == "My Note"


class TestRunIncrementalModifyDoc:
    def test_modified_doc_title_updated(self, incremental_db):
        db_path, corpus = incremental_db
        hub = corpus / "autoscriptstudio" / "hub.md"
        hub.write_text(
            dedent("""\
                ---
                title: Updated Hub
                status: active
                doc_type: hub
                entity: autoscriptstudio
                ---
                # Updated Hub
                New content.
            """)
        )
        _commit_all(corpus, "update hub title")

        conn = db.connect(db_path)
        try:
            run_incremental(conn, "HEAD~1", corpus, {})
        finally:
            conn.close()

        conn = db.connect(db_path)
        row = conn.execute(
            "SELECT title FROM doc WHERE path='autoscriptstudio/hub.md'"
        ).fetchone()
        conn.close()
        assert row["title"] == "Updated Hub"

    def test_stale_edges_cleared_on_modify(self, incremental_db):
        db_path, corpus = incremental_db
        hub = corpus / "autoscriptstudio" / "hub.md"
        # Add a tag to get a TAGGED edge, then remove it
        hub.write_text(
            dedent("""\
                ---
                title: Hub
                status: active
                doc_type: hub
                entity: autoscriptstudio
                tags: [type/hub]
                ---
                # Hub
            """)
        )
        _commit_all(corpus, "add tag")

        conn = db.connect(db_path)
        run_incremental(conn, "HEAD~1", corpus, {})
        conn.close()

        # Verify TAGGED edge was created
        conn = db.connect(db_path)
        count_before = conn.execute(
            "SELECT COUNT(*) FROM edge WHERE edge_type='TAGGED' AND source_id='autoscriptstudio/hub.md'"
        ).fetchone()[0]
        conn.close()
        assert count_before == 1

        # Now remove the tag
        hub.write_text(
            dedent("""\
                ---
                title: Hub
                status: active
                doc_type: hub
                entity: autoscriptstudio
                ---
                # Hub
            """)
        )
        _commit_all(corpus, "remove tag")

        conn = db.connect(db_path)
        run_incremental(conn, "HEAD~1", corpus, {})
        conn.close()

        conn = db.connect(db_path)
        count_after = conn.execute(
            "SELECT COUNT(*) FROM edge WHERE edge_type='TAGGED' AND source_id='autoscriptstudio/hub.md'"
        ).fetchone()[0]
        conn.close()
        assert count_after == 0


class TestRunIncrementalDeleteDoc:
    def test_deleted_doc_removed_from_db(self, incremental_db):
        db_path, corpus = incremental_db
        hub = corpus / "autoscriptstudio" / "hub.md"
        hub.unlink()
        _commit_all(corpus, "delete hub")

        conn = db.connect(db_path)
        try:
            run_incremental(conn, "HEAD~1", corpus, {})
        finally:
            conn.close()

        conn = db.connect(db_path)
        row = conn.execute(
            "SELECT 1 FROM doc WHERE path='autoscriptstudio/hub.md'"
        ).fetchone()
        conn.close()
        assert row is None


class TestRunIncrementalRenameDoc:
    def test_renamed_doc_updates_path(self, incremental_db):
        db_path, corpus = incremental_db
        old = corpus / "autoscriptstudio" / "hub.md"
        new = corpus / "autoscriptstudio" / "main-hub.md"
        old.rename(new)
        _commit_all(corpus, "rename hub")

        conn = db.connect(db_path)
        try:
            run_incremental(conn, "HEAD~1", corpus, {})
        finally:
            conn.close()

        conn = db.connect(db_path)
        assert conn.execute("SELECT 1 FROM doc WHERE path='autoscriptstudio/main-hub.md'").fetchone() is not None
        assert conn.execute("SELECT 1 FROM doc WHERE path='autoscriptstudio/hub.md'").fetchone() is None
        conn.close()


class TestRunIncrementalNonMarkdown:
    def test_non_markdown_files_are_skipped(self, incremental_db):
        db_path, corpus = incremental_db
        (corpus / "autoscriptstudio" / "image.png").write_bytes(b"\x89PNG")
        _commit_all(corpus, "add image")

        conn = db.connect(db_path)
        summary = run_incremental(conn, "HEAD~1", corpus, {})
        conn.close()

        assert summary["added"] == 0
        assert summary["failed"] == 0
