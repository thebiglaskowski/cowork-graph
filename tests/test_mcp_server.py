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
                result = await client.call_tool(
                    "search_docs", {"query": "xyzzy_nonexistent_42"}
                )
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
    def test_returns_nonempty_list(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_active", {})
                return result.data

        data = _run(check())
        assert isinstance(data, list)
        assert len(data) > 0


class TestListBlocked:
    def test_returns_list(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("list_blocked", {})
                return result.data

        data = _run(check())
        assert isinstance(data, list)


class TestProjectState:
    def test_returns_state_for_known_project(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool(
                    "project_state", {"slug": "skunkworks"}
                )
                return result.data

        data = _run(check())
        assert data is not None

    def test_returns_none_for_unknown_project(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool(
                    "project_state", {"slug": "nonexistent-zzz"}
                )
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

    def test_returns_none_for_unknown_person(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("who", {"name": "Nobody Knowsbody"})
                return result.data

        data = _run(check())
        assert data is None


class TestDecisions:
    def test_returns_decisions(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("decisions", {})
                return result.data

        data = _run(check())
        assert isinstance(data, list)
        assert len(data) >= 1


class TestAudit:
    def test_returns_stub(self, mcp_app):
        from fastmcp import Client

        async def check():
            async with Client(mcp_app) as client:
                result = await client.call_tool("audit", {})
                return result.data

        data = _run(check())
        assert data is not None
        assert data["status"] == "not_implemented"
