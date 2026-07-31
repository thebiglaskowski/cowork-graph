"""Tests for last-indexed-SHA tracking (the self-healing update watermark).

The property under test: an update run that does not complete must NOT advance
the stored SHA, so the next run re-covers the same range. This is what stops a
killed nightly job from silently dropping commits from the graph forever.
"""

from __future__ import annotations

import subprocess

import pytest

from cowork_graph import db, incremental


def _git(repo, *args):
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo),
        },
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.md").write_text("# A\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    return r


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "g.db")
    yield c
    c.close()


class TestShaRoundTrip:
    def test_unset_returns_none(self, conn):
        assert db.get_last_indexed_sha(conn) is None

    def test_set_then_get(self, conn):
        db.set_last_indexed_sha(conn, "abc123")
        assert db.get_last_indexed_sha(conn) == "abc123"

    def test_set_overwrites(self, conn):
        db.set_last_indexed_sha(conn, "abc123")
        db.set_last_indexed_sha(conn, "def456")
        assert db.get_last_indexed_sha(conn) == "def456"

    def test_survives_reconnect(self, tmp_path):
        p = tmp_path / "persist.db"
        c1 = db.connect(p)
        db.set_last_indexed_sha(c1, "abc123")
        c1.commit()
        c1.close()
        c2 = db.connect(p)
        assert db.get_last_indexed_sha(c2) == "abc123"
        c2.close()


class TestGitHelpers:
    def test_head_sha_returns_full_sha(self, repo):
        sha = incremental.head_sha(repo)
        assert sha and len(sha) == 40

    def test_head_sha_none_outside_repo(self, tmp_path):
        d = tmp_path / "notarepo"
        d.mkdir()
        assert incremental.head_sha(d) is None

    def test_commit_exists_true_for_head(self, repo):
        assert incremental.commit_exists(incremental.head_sha(repo), repo)

    def test_commit_exists_false_for_unknown(self, repo):
        assert not incremental.commit_exists("0" * 40, repo)

    def test_orphaned_commit_still_diffable(self, repo):
        """A commit unreachable from HEAD is still a valid diff base.

        git diff compares trees, not ancestry — this is why a rebase (which
        orphans the pre-rebase tip) does not force a full rebuild.
        """
        orphan = incremental.head_sha(repo)
        (repo / "b.md").write_text("# B\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "two")
        _git(repo, "reset", "--hard", "HEAD~1", "-q")
        # orphan is now not an ancestor of HEAD, but still present
        assert incremental.commit_exists(orphan, repo)
        diff = incremental.git_diff_files(orphan, repo)
        assert diff.added == [] and diff.modified == []


@pytest.fixture
def cli_env(repo, tmp_path, monkeypatch):
    """Point the CLI's config at a scratch git repo + scratch DB."""
    db_path = tmp_path / "graph.db"
    monkeypatch.setenv("COWORK_GRAPH_DB_PATH", str(db_path))

    import cowork_graph.config as cfg_mod
    original_load = cfg_mod.load

    def patched_load(config_path=None):
        cfg = original_load(config_path=tmp_path / "config.toml")
        cfg.cowork_root = repo
        cfg.db_path = db_path
        return cfg

    monkeypatch.setattr(cfg_mod, "load", patched_load)
    return db_path


class TestWatermarkNotAdvancedOnFailure:
    """The self-healing property: interruption must leave the SHA untouched.

    Exercises the real _cmd_update path, not a stand-in for it.
    """

    def test_sha_unchanged_when_update_is_interrupted(self, repo, cli_env, monkeypatch):
        from cowork_graph import cli
        from cowork_graph import incremental as inc

        # Establish a baseline the way a real build would.
        assert cli._cmd_build([]) == 0
        conn = db.connect(cli_env)
        baseline = db.get_last_indexed_sha(conn)
        conn.close()
        assert baseline == incremental.head_sha(repo)

        # New commit, then an update that dies partway through.
        (repo / "new.md").write_text("# new\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "new work")

        def killed(*a, **k):
            raise RuntimeError("scheduled task exited, taking the job with it")

        monkeypatch.setattr(inc, "run_incremental", killed)

        with pytest.raises(RuntimeError):
            cli._cmd_update(["--since", "HEAD~1"])

        conn = db.connect(cli_env)
        assert db.get_last_indexed_sha(conn) == baseline, (
            "watermark advanced despite the run being interrupted — "
            "the next run would skip this commit forever"
        )
        conn.close()

    def test_sha_advances_on_success(self, repo, cli_env):
        from cowork_graph import cli

        assert cli._cmd_build([]) == 0
        (repo / "new.md").write_text("# new\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "new work")

        assert cli._cmd_update(["--since", "HEAD~1"]) == 0
        conn = db.connect(cli_env)
        assert db.get_last_indexed_sha(conn) == incremental.head_sha(repo)
        conn.close()

    def test_successful_update_stamps_built_at_and_kind(self, repo, cli_env):
        """built_at must move on incremental runs, not just full builds.

        A stale built_at after a successful incremental is what made the
        missed nightly syncs invisible.
        """
        from cowork_graph import cli

        assert cli._cmd_build([]) == 0
        conn = db.connect(cli_env)
        before = conn.execute(
            "SELECT value FROM schema_meta WHERE key='built_at'"
        ).fetchone()[0]
        conn.close()

        (repo / "new.md").write_text("# new\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "new work")
        assert cli._cmd_update(["--since", "HEAD~1"]) == 0

        conn = db.connect(cli_env)
        rows = dict(conn.execute("SELECT key, value FROM schema_meta").fetchall())
        conn.close()
        assert rows["built_at"] > before, "built_at did not advance on incremental"
        assert rows["build_kind"] == "incremental"

    def test_interrupted_run_is_recovered_by_next_run(self, repo, cli_env, monkeypatch):
        """End-to-end: a dropped commit still lands in the graph afterwards."""
        from cowork_graph import cli
        from cowork_graph import incremental as inc

        assert cli._cmd_build([]) == 0

        (repo / "missed.md").write_text("# missed\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "missed")

        # Toggle rather than monkeypatch.undo() — undo() would also revert the
        # config patch from cli_env and send the next call at the real corpus.
        real_run = inc.run_incremental
        state = {"kill": True}

        def maybe_killed(*a, **k):
            if state["kill"]:
                raise RuntimeError("killed")
            return real_run(*a, **k)

        monkeypatch.setattr(inc, "run_incremental", maybe_killed)

        with pytest.raises(RuntimeError):
            cli._cmd_update(["--since", "HEAD~1"])

        state["kill"] = False

        # A later, unrelated commit triggers the next run.
        (repo / "later.md").write_text("# later\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "later")
        assert cli._cmd_update(["--since", "HEAD~1"]) == 0

        conn = db.connect(cli_env)
        paths = {r[0] for r in conn.execute("SELECT path FROM doc").fetchall()}
        conn.close()
        assert "missed.md" in paths, "the dropped commit was never recovered"
        assert "later.md" in paths

    def test_gap_is_covered_after_missed_run(self, repo):
        """Two commits land but only the second triggers a run.

        Diffing from the stored SHA must surface files from BOTH commits —
        diffing from HEAD~1 would surface only the second.
        """
        base = incremental.head_sha(repo)

        (repo / "missed.md").write_text("# missed\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "missed run")

        (repo / "latest.md").write_text("# latest\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "latest")

        from_watermark = incremental.git_diff_files(base, repo)
        from_head1 = incremental.git_diff_files("HEAD~1", repo)

        assert set(from_watermark.added) == {"missed.md", "latest.md"}
        assert set(from_head1.added) == {"latest.md"}  # the bug this fixes
