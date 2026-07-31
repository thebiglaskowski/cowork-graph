"""Tests for mcp_server.py — FastMCP client verifies tool registration and response shapes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cowork_graph.cli import _cmd_build

CORPUS = Path(__file__).parent / "fixtures" / "corpus"
EXPECTED_TOOLS = {
    "search_docs",
    "get_doc",
    "list_active",
    "list_blocked",
    "project_state",
    "who",
    "decisions",
    "audit",
}


@pytest.fixture(scope="module")
def mcp_app(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("mcp_db")
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
        from cowork_graph.mcp_server import mcp

        yield mcp
    finally:
        cfg_mod.load = original_load


def _run(coro):
    return asyncio.run(coro)


class TestToolRegistration:
    def test_all_eight_tools_registered(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                tools = await client.list_tools()
                return {t.name for t in tools}

        names = _run(check())
        assert names == EXPECTED_TOOLS


class TestSearchDocs:
    def test_returns_hits_for_matching_query(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("search_docs", {"query": "Jane"})
                return result.data

        data = _run(check())
        assert isinstance(data, list)
        assert len(data) > 0

    def test_no_results_for_nonsense_query(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("search_docs", {"query": "xyzzy_nonexistent_42"})
                return result.data

        data = _run(check())
        assert data == []


class TestGetDoc:
    def test_returns_doc_for_known_path(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool(
                    "get_doc",
                    {"path": "autoscriptstudio/autoscript-hub.md"},
                )
                return result.data

        data = _run(check())
        assert data is not None

    def test_returns_none_for_missing_path(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("get_doc", {"path": "no/such/doc.md"})
                return result.data

        data = _run(check())
        assert data is None


class TestListActive:
    def test_returns_nonempty_envelope(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_active", {})
                return result.data

        data = _run(check())
        assert isinstance(data["items"], list)
        assert len(data["items"]) > 0
        assert data["total"] == len(data["items"])
        assert "note" not in data

    def test_items_keep_doc_summary_shape(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_active", {})
                return result.data

        item = _run(check())["items"][0]
        assert set(item) == {"path", "title", "status", "doc_type", "last_modified"}

    def test_limit_truncates_with_note(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_active", {"limit": 1})
                return result.data

        data = _run(check())
        assert len(data["items"]) == 1
        assert data["total"] > 1
        assert f"...and {data['total'] - 1} more" in data["note"]
        assert "raise limit" in data["note"]

    def test_count_only_returns_no_items(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_active", {"count_only": True})
                return result.data

        data = _run(check())
        assert data["items"] == []
        assert data["total"] > 0


class TestListBlocked:
    def test_returns_envelope(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_blocked", {})
                return result.data

        data = _run(check())
        assert isinstance(data["items"], list)
        assert data["total"] == len(data["items"])


class TestProjectState:
    def test_returns_state_for_known_project(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("project_state", {"slug": "skunkworks"})
                return result.data

        data = _run(check())
        assert data is not None

    def test_returns_none_for_unknown_project(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("project_state", {"slug": "nonexistent-zzz"})
                return result.data

        data = _run(check())
        assert data is None


class TestWho:
    def test_finds_known_person(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("who", {"name": "Jane Doe"})
                return result.data

        data = _run(check())
        assert data is not None
        assert data["mentions_total"] == len(data["mentions"])
        assert "note" not in data

    def test_mentions_limit_truncates_with_note(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool(
                    "who", {"name": "Jane Doe", "mentions_limit": 0}
                )
                return result.data

        data = _run(check())
        assert data["mentions"] == []
        assert data["mentions_total"] > 0
        assert "raise mentions_limit" in data["note"]

    def test_returns_none_for_unknown_person(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("who", {"name": "Nobody Knowsbody"})
                return result.data

        data = _run(check())
        assert data is None


class TestDecisions:
    def test_returns_decisions_envelope(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("decisions", {})
                return result.data

        data = _run(check())
        assert isinstance(data["items"], list)
        assert len(data["items"]) >= 1
        assert data["total"] == len(data["items"])
        assert "note" not in data

    def test_limit_truncates_with_note(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("decisions", {"limit": 0})
                return result.data

        data = _run(check())
        assert data["items"] == []
        assert data["total"] >= 1
        assert f"...and {data['total']} more" in data["note"]

    def test_count_only(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("decisions", {"count_only": True})
                return result.data

        data = _run(check())
        assert data["items"] == []
        assert data["total"] >= 1


class TestAudit:
    def test_returns_ok(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("audit", {})
                return result.data

        data = _run(check())
        assert data is not None
        assert data["status"] == "ok"
        assert "total_findings" in data
        assert "findings" in data


class TestServeCLI:
    """`mcp serve` arg parsing — patch the server main so no real server starts."""

    @staticmethod
    def _capture(monkeypatch):
        import cowork_graph.mcp_server as srv

        captured: dict = {}
        monkeypatch.setattr(srv, "main", lambda **kw: captured.update(kw))
        return captured

    def test_stdio_is_default(self, monkeypatch):
        from cowork_graph import cli

        captured = self._capture(monkeypatch)
        assert cli._cmd_mcp(["serve"]) == 0
        assert captured == {}  # stdio path calls main() with no kwargs

    def test_bare_mcp_defaults_to_stdio(self, monkeypatch):
        from cowork_graph import cli

        captured = self._capture(monkeypatch)
        assert cli._cmd_mcp([]) == 0
        assert captured == {}

    def test_http_uses_defaults(self, monkeypatch):
        from cowork_graph import cli

        captured = self._capture(monkeypatch)
        assert cli._cmd_mcp(["serve", "--http"]) == 0
        assert captured == {
            "transport": "http",
            "host": "127.0.0.1",
            "port": 8765,
            "path": "/mcp",
        }

    def test_http_transport_is_stateless(self, monkeypatch):
        """A restart must not strand connected clients on dead session IDs.

        Sessionful streamable HTTP 404s every client whose session predates a
        server restart — the desktop extension's mcp-remote proxy then hangs
        on every tool call until manually restarted.
        """
        import cowork_graph.mcp_server as srv

        captured: dict = {}
        monkeypatch.setattr(srv.mcp, "run", lambda **kw: captured.update(kw))
        srv.main(transport="http")
        assert captured["stateless_http"] is True

    def test_stdio_transport_has_no_http_kwargs(self, monkeypatch):
        import cowork_graph.mcp_server as srv

        captured: dict = {}
        monkeypatch.setattr(srv.mcp, "run", lambda **kw: captured.update(kw))
        srv.main()
        assert captured == {}

    def test_http_parses_host_port_path(self, monkeypatch):
        from cowork_graph import cli

        captured = self._capture(monkeypatch)
        rc = cli._cmd_mcp(
            ["serve", "--http", "--host", "0.0.0.0", "--port", "9999", "--path", "/x"]
        )
        assert rc == 0
        assert captured == {
            "transport": "http",
            "host": "0.0.0.0",
            "port": 9999,
            "path": "/x",
        }
