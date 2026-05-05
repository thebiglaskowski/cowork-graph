"""Tests for config.py — uses tmp_path to avoid touching real ~/.config."""

from pathlib import Path

from cowork_graph.config import (
    _DEFAULT_COWORK_ROOT,
    _DEFAULT_DB_PATH,
    _DEFAULT_FAIL_THRESHOLD_PCT,
    _DEFAULT_LOG_PATH,
    load,
)


class TestLoadDefaults:
    def test_defaults_returned_when_no_file(self, tmp_path):
        cfg = load(config_path=tmp_path / "config.toml")
        assert cfg.cowork_root == Path(_DEFAULT_COWORK_ROOT)
        assert cfg.db_path == Path(_DEFAULT_DB_PATH).expanduser()
        assert cfg.log_path == Path(_DEFAULT_LOG_PATH).expanduser()
        assert cfg.parser.fail_threshold_pct == _DEFAULT_FAIL_THRESHOLD_PCT

    def test_default_file_generated_on_first_run(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        assert not cfg_path.exists()
        load(config_path=cfg_path)
        assert cfg_path.exists()
        content = cfg_path.read_text()
        assert "cowork-graph" in content

    def test_generated_file_is_valid_toml(self, tmp_path):
        import tomllib

        cfg_path = tmp_path / "config.toml"
        load(config_path=cfg_path)
        with cfg_path.open("rb") as f:
            tomllib.load(f)  # must not raise


class TestLoadFromFile:
    def _write(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        return path

    def test_custom_cowork_root(self, tmp_path):
        cfg_path = self._write(
            tmp_path / "config.toml",
            'cowork_root = "/custom/cowork"\n',
        )
        cfg = load(config_path=cfg_path)
        assert cfg.cowork_root == Path("/custom/cowork")

    def test_custom_db_path_expanded(self, tmp_path):
        cfg_path = self._write(
            tmp_path / "config.toml",
            'db_path = "~/mydb/graph.db"\n',
        )
        cfg = load(config_path=cfg_path)
        assert cfg.db_path == Path("~/mydb/graph.db").expanduser()
        assert not str(cfg.db_path).startswith("~")

    def test_custom_fail_threshold(self, tmp_path):
        cfg_path = self._write(
            tmp_path / "config.toml",
            "[parser]\nfail_threshold_pct = 10\n",
        )
        cfg = load(config_path=cfg_path)
        assert cfg.parser.fail_threshold_pct == 10

    def test_partial_file_fills_remaining_defaults(self, tmp_path):
        cfg_path = self._write(
            tmp_path / "config.toml",
            'cowork_root = "/only/this/set"\n',
        )
        cfg = load(config_path=cfg_path)
        assert cfg.parser.fail_threshold_pct == _DEFAULT_FAIL_THRESHOLD_PCT


class TestEnvVarOverride:
    def test_env_var_overrides_db_path(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.toml"
        monkeypatch.setenv("COWORK_GRAPH_DB_PATH", "/tmp/test-graph.db")
        cfg = load(config_path=cfg_path)
        assert cfg.db_path == Path("/tmp/test-graph.db")

    def test_env_var_absent_uses_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("COWORK_GRAPH_DB_PATH", raising=False)
        cfg = load(config_path=tmp_path / "config.toml")
        assert cfg.db_path == Path(_DEFAULT_DB_PATH).expanduser()
