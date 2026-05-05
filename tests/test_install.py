"""Tests for install.py — mock filesystem writes, assert correct config shape per surface."""

from __future__ import annotations

import json
from pathlib import Path


from cowork_graph.install import (
    InstallResult,
    _SERVER_NAME,
    _WSL_REPO,
    _cc_ps_entry,
    _cc_wsl_entry,
    _desktop_entry,
    _install_to,
    install,
    print_results,
)


class TestEntryShapes:
    def test_cc_wsl_entry_has_type_stdio(self):
        entry = _cc_wsl_entry()
        assert entry["type"] == "stdio"

    def test_cc_wsl_entry_uses_uv_command(self):
        entry = _cc_wsl_entry()
        assert entry["command"] == "uv"
        assert "run" in entry["args"]
        assert "--directory" in entry["args"]
        assert _WSL_REPO in entry["args"]
        assert "mcp" in entry["args"]
        assert "serve" in entry["args"]

    def test_desktop_entry_has_no_type_field(self):
        entry = _desktop_entry()
        assert "type" not in entry

    def test_desktop_entry_uses_wsl_command(self):
        entry = _desktop_entry()
        assert entry["command"] == "wsl"
        assert "-e" in entry["args"]
        assert "uv" in entry["args"]

    def test_cc_ps_entry_has_type_stdio(self):
        entry = _cc_ps_entry()
        assert entry["type"] == "stdio"

    def test_cc_ps_entry_uses_wsl_command(self):
        entry = _cc_ps_entry()
        assert entry["command"] == "wsl"


class TestInstallTo:
    def _fake_config(self, tmp_path: Path, content: dict | None = None) -> Path:
        p = tmp_path / "test_config.json"
        p.write_text(json.dumps(content or {}), encoding="utf-8")
        return p

    def test_installs_to_empty_config(self, tmp_path):
        cfg = self._fake_config(tmp_path)
        result = _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        assert result.status == "installed"
        data = json.loads(cfg.read_text())
        assert _SERVER_NAME in data["mcpServers"]

    def test_installs_correct_entry_shape(self, tmp_path):
        cfg = self._fake_config(tmp_path)
        _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        data = json.loads(cfg.read_text())
        entry = data["mcpServers"][_SERVER_NAME]
        assert entry["type"] == "stdio"
        assert "mcp" in entry["args"]
        assert "serve" in entry["args"]

    def test_updates_existing_entry(self, tmp_path):
        cfg = self._fake_config(
            tmp_path,
            {"mcpServers": {_SERVER_NAME: {"command": "old-command"}}},
        )
        result = _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        assert result.status == "updated"

    def test_idempotent_second_install_is_updated(self, tmp_path):
        cfg = self._fake_config(tmp_path)
        _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        result2 = _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        assert result2.status == "updated"
        # Only one entry in mcpServers — no duplicates
        data = json.loads(cfg.read_text())
        assert len([k for k in data["mcpServers"] if k == _SERVER_NAME]) == 1

    def test_skips_when_config_missing(self, tmp_path):
        result = _install_to(tmp_path / "nonexistent.json", _cc_wsl_entry(), "code-wsl")
        assert result.status == "skipped"
        assert result.reason == "config not found"

    def test_preserves_other_servers(self, tmp_path):
        cfg = self._fake_config(
            tmp_path,
            {"mcpServers": {"other-server": {"command": "keep-me"}}},
        )
        _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        data = json.loads(cfg.read_text())
        assert "other-server" in data["mcpServers"]
        assert data["mcpServers"]["other-server"]["command"] == "keep-me"

    def test_preserves_other_top_level_keys(self, tmp_path):
        cfg = self._fake_config(tmp_path, {"someOtherKey": "value"})
        _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        data = json.loads(cfg.read_text())
        assert data["someOtherKey"] == "value"

    def test_desktop_entry_written_without_type(self, tmp_path):
        cfg = self._fake_config(tmp_path)
        _install_to(cfg, _desktop_entry(), "desktop")
        data = json.loads(cfg.read_text())
        entry = data["mcpServers"][_SERVER_NAME]
        assert "type" not in entry

    def test_skips_on_invalid_json(self, tmp_path):
        cfg = tmp_path / "bad.json"
        cfg.write_text("not json {{{{", encoding="utf-8")
        result = _install_to(cfg, _cc_wsl_entry(), "code-wsl")
        assert result.status == "skipped"
        assert "unreadable" in (result.reason or "")


class TestInstallDispatch:
    def test_no_flags_returns_empty(self, tmp_path):
        results = install()
        assert results == []

    def test_code_wsl_flag_targets_correct_surface(self, monkeypatch, tmp_path):
        fake_cfg = tmp_path / ".claude.json"
        fake_cfg.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("cowork_graph.install._CC_WSL_CONFIG", fake_cfg)
        results = install(code_wsl=True)
        assert len(results) == 1
        assert results[0].surface == "code-wsl"

    def test_desktop_flag_targets_correct_surface(self, monkeypatch, tmp_path):
        fake_cfg = tmp_path / "desktop.json"
        fake_cfg.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("cowork_graph.install._DESKTOP_CONFIG", fake_cfg)
        results = install(desktop=True)
        assert len(results) == 1
        assert results[0].surface == "desktop"

    def test_all_flags_returns_three_results(self, monkeypatch, tmp_path):
        for attr, name in [
            ("_CC_WSL_CONFIG", "wsl.json"),
            ("_DESKTOP_CONFIG", "desktop.json"),
            ("_CC_PS_CONFIG", "ps.json"),
        ]:
            p = tmp_path / name
            p.write_text("{}", encoding="utf-8")
            monkeypatch.setattr(f"cowork_graph.install.{attr}", p)
        results = install(desktop=True, code_wsl=True, code_ps=True)
        assert len(results) == 3


class TestPrintResults:
    def test_installed_line_format(self, capsys):
        print_results([InstallResult(surface="code-wsl", status="installed")])
        assert "[installed] code-wsl" in capsys.readouterr().out

    def test_updated_line_format(self, capsys):
        print_results([InstallResult(surface="desktop", status="updated")])
        assert "[updated] desktop" in capsys.readouterr().out

    def test_skipped_includes_reason(self, capsys):
        print_results(
            [InstallResult(surface="code-ps", status="skipped", reason="config not found")]
        )
        out = capsys.readouterr().out
        assert "[skipped: config not found] code-ps" in out
