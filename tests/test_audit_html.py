"""Tests for cowork_graph.audit_html."""

from __future__ import annotations

import html.parser
from pathlib import Path

import pytest

from cowork_graph import db
from cowork_graph.audit_html import write_html_report

# ---------------------------------------------------------------------------
# Shared fixture audit result
# ---------------------------------------------------------------------------

_FIXTURE_RESULT: dict = {
    "run_at": "2026-05-09T12:00:00+00:00",
    "built_at": "2026-05-09T11:00:00+00:00",
    "total_findings": 4,
    "suppressed": {},
    "summary": {
        "broken_links": 2,
        "ghost_projects": 1,
        "orphan_docs": 1,
        "one_way_edges": 0,
        "stale_active_docs": 0,
        "inconsistent_hub_state": 0,
        "ghost_people": 0,
        "decision_drift": 0,
        "tag_drift": 0,
        "decisions_format_drift": 0,
    },
    "findings": {
        "broken_links": [
            {
                "source_doc": "autoscriptstudio/hub.md",
                "link_text": "plan",
                "link_target": "plan.md",
            },
            {
                "source_doc": "personal/note.md",
                "link_text": "ref",
                "link_target": "missing.md",
            },
        ],
        "ghost_projects": [{"slug": "skunkworks"}],
        "orphan_docs": [{"path": "personal/orphan.md", "title": "Orphan Note"}],
        "one_way_edges": [],
        "stale_active_docs": [],
        "inconsistent_hub_state": [],
        "ghost_people": [],
        "decision_drift": [],
        "tag_drift": [],
        "decisions_format_drift": [],
    },
    "report_written": None,
}

# ---------------------------------------------------------------------------
# Minimal DB fixture — schema + a handful of doc rows for the status chart
# ---------------------------------------------------------------------------

_VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


@pytest.fixture
def minimal_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    conn.execute("BEGIN")
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
        ("built_at", "2026-05-09T11:00:00+00:00"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
        ("build_kind", "full"),
    )
    for path, status in [
        ("a.md", "active"),
        ("b.md", "active"),
        ("c.md", "queued"),
        ("d.md", "done"),
        ("e.md", "reference"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO doc"
            " (path, title, status, doc_type, word_count, link_count, parse_status)"
            " VALUES (?, ?, ?, 'note', 0, 0, 'ok')",
            (path, path, status),
        )
    conn.execute("COMMIT")
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 1. Fixture-driven render tests
# ---------------------------------------------------------------------------


class TestRenderFixture:
    def test_doctype_present(self, tmp_path: Path, minimal_db: Path) -> None:
        out = tmp_path / "audit.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out)
        assert out.read_text().startswith("<!DOCTYPE html>")

    def test_h1_present(self, tmp_path: Path, minimal_db: Path) -> None:
        out = tmp_path / "audit.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out)
        assert "<h1>cowork-graph audit</h1>" in out.read_text()

    def test_toc_has_entry_per_rule(self, tmp_path: Path, minimal_db: Path) -> None:
        out = tmp_path / "audit.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out)
        content = out.read_text()
        for rule in _FIXTURE_RESULT["findings"]:
            anchor = rule.replace("_", "-")
            assert f'href="#{anchor}"' in content, f"missing TOC link for {rule}"

    def test_finding_titles_present(self, tmp_path: Path, minimal_db: Path) -> None:
        out = tmp_path / "audit.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out)
        content = out.read_text()
        assert "autoscriptstudio/hub.md" in content
        assert "skunkworks" in content
        assert "personal/orphan.md" in content

    def test_no_template_leak(self, tmp_path: Path, minimal_db: Path) -> None:
        out = tmp_path / "audit.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out)
        content = out.read_text()
        assert "{{" not in content
        assert "}}" not in content


# ---------------------------------------------------------------------------
# 2. Well-formedness
# ---------------------------------------------------------------------------


class _TagChecker(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in _VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_ELEMENTS:
            return
        if not self.stack:
            self.errors.append(f"unexpected </{tag}> with empty stack")
        elif self.stack[-1] != tag:
            self.errors.append(f"mismatched: expected </{self.stack[-1]}> got </{tag}>")
        else:
            self.stack.pop()


class TestWellFormedness:
    def test_balanced_tags(self, tmp_path: Path, minimal_db: Path) -> None:
        out = tmp_path / "audit.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out)
        checker = _TagChecker()
        checker.feed(out.read_text())
        assert checker.errors == [], f"tag errors: {checker.errors}"
        assert checker.stack == [], f"unclosed tags: {checker.stack}"


# ---------------------------------------------------------------------------
# 3. HTML escaping
# ---------------------------------------------------------------------------


class TestEscaping:
    def test_xss_in_slug_is_escaped(self, tmp_path: Path, minimal_db: Path) -> None:
        result = {
            **_FIXTURE_RESULT,
            "findings": {
                **_FIXTURE_RESULT["findings"],
                "ghost_projects": [{"slug": "<script>alert(1)</script>"}],
            },
            "summary": {**_FIXTURE_RESULT["summary"], "ghost_projects": 1},
            "total_findings": 3,
        }
        out = tmp_path / "audit_xss.html"
        write_html_report(result, minimal_db, out)
        content = out.read_text()
        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;" in content

    def test_xss_in_source_doc_is_escaped(self, tmp_path: Path, minimal_db: Path) -> None:
        result = {
            **_FIXTURE_RESULT,
            "findings": {
                **_FIXTURE_RESULT["findings"],
                "broken_links": [
                    {
                        "source_doc": "<img src=x onerror=alert(1)>",
                        "link_text": "x",
                        "link_target": "y.md",
                    }
                ],
            },
            "summary": {**_FIXTURE_RESULT["summary"], "broken_links": 1},
            "total_findings": 2,
        }
        out = tmp_path / "audit_xss2.html"
        write_html_report(result, minimal_db, out)
        content = out.read_text()
        assert "<img src=x" not in content
        assert "&lt;img" in content


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_byte_identical_output(self, tmp_path: Path, minimal_db: Path) -> None:
        out1 = tmp_path / "audit1.html"
        out2 = tmp_path / "audit2.html"
        write_html_report(_FIXTURE_RESULT, minimal_db, out1)
        write_html_report(_FIXTURE_RESULT, minimal_db, out2)
        assert out1.read_bytes() == out2.read_bytes()


# ---------------------------------------------------------------------------
# 5. CLI integration smoke test
# ---------------------------------------------------------------------------


class TestCliIntegration:
    def test_html_flag_creates_file(self, tmp_path: Path) -> None:
        """--html creates an HTML file at the expected path and includes the H1."""
        from datetime import datetime, timezone

        import cowork_graph.config as cfg_mod
        from cowork_graph.cli import _cmd_audit, _cmd_build

        CORPUS = Path(__file__).parent / "fixtures" / "corpus"
        db_path = tmp_path / "graph.db"
        audit_root = tmp_path / "audit-root"

        original_load = cfg_mod.load
        # First call (build) uses real corpus; second call (audit) redirects output
        _roots = iter([CORPUS, audit_root])

        def patched_load(config_path=None):  # type: ignore[override]
            cfg = original_load(config_path=tmp_path / "config.toml")
            cfg.cowork_root = next(_roots)
            cfg.db_path = db_path
            return cfg

        cfg_mod.load = patched_load
        try:
            assert _cmd_build([]) == 0
            assert _cmd_audit(["--html"]) == 0
        finally:
            cfg_mod.load = original_load

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        html_path = (
            audit_root / "claude-environment" / "cowork-graph" / "audits" / f"{date_str}-audit.html"
        )
        assert html_path.exists(), f"expected HTML at {html_path}"
        content = html_path.read_text()
        assert "<h1>cowork-graph audit</h1>" in content

    def test_write_only_produces_no_html(self, tmp_path: Path) -> None:
        """--write without --html must not create an HTML file (regression guard)."""
        from datetime import datetime, timezone

        import cowork_graph.config as cfg_mod
        from cowork_graph.cli import _cmd_audit, _cmd_build

        CORPUS = Path(__file__).parent / "fixtures" / "corpus"
        db_path = tmp_path / "graph.db"
        audit_root = tmp_path / "audit-root2"

        original_load = cfg_mod.load
        _roots = iter([CORPUS, audit_root])

        def patched_load(config_path=None):  # type: ignore[override]
            cfg = original_load(config_path=tmp_path / "config.toml")
            cfg.cowork_root = next(_roots)
            cfg.db_path = db_path
            return cfg

        cfg_mod.load = patched_load
        try:
            assert _cmd_build([]) == 0
            assert _cmd_audit(["--write"]) == 0
        finally:
            cfg_mod.load = original_load

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        html_path = (
            audit_root / "claude-environment" / "cowork-graph" / "audits" / f"{date_str}-audit.html"
        )
        assert not html_path.exists(), "HTML file must not be created without --html"
