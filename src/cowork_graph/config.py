"""Load per-machine config from ~/.config/cowork-graph/config.toml.

Generates the file with commented defaults on first run if missing.
Env-var overrides: COWORK_GRAPH_DB_PATH overrides config.db_path.
Uses stdlib tomllib (Python 3.11+, no extra dependency).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_CONFIG_DIR = Path.home() / ".config" / "cowork-graph"
_CONFIG_PATH = _CONFIG_DIR / "config.toml"

_DEFAULT_COWORK_ROOT = "/mnt/c/Users/joela/cowork"
_DEFAULT_DB_PATH = "~/.local/share/cowork-graph/graph.db"
_DEFAULT_LOG_PATH = "~/.local/share/cowork-graph/parse.log"
_DEFAULT_FAIL_THRESHOLD_PCT = 5

_DEFAULT_TOML = """\
# cowork-graph per-machine configuration
# Generated on first run. Edit as needed.

# Absolute path to the root of the cowork repository on this machine.
# cowork_root = "/mnt/c/Users/joela/cowork"

# Path to the SQLite graph database. ~ is expanded.
# db_path = "~/.local/share/cowork-graph/graph.db"

# Path to the parse log. ~ is expanded.
# log_path = "~/.local/share/cowork-graph/parse.log"

[parser]
# Exit non-zero from `cowork-graph build` if more than this percentage
# of docs fail to parse.
# fail_threshold_pct = 5
"""


@dataclass
class ParserConfig:
    fail_threshold_pct: int = _DEFAULT_FAIL_THRESHOLD_PCT


@dataclass
class Config:
    cowork_root: Path = field(default_factory=lambda: Path(_DEFAULT_COWORK_ROOT))
    db_path: Path = field(default_factory=lambda: Path(_DEFAULT_DB_PATH).expanduser())
    log_path: Path = field(default_factory=lambda: Path(_DEFAULT_LOG_PATH).expanduser())
    parser: ParserConfig = field(default_factory=ParserConfig)


def load(config_path: Path | None = None) -> Config:
    """Load config, generating the file with defaults if absent."""
    path = config_path or _CONFIG_PATH

    if not path.exists():
        _write_default(path)

    with path.open("rb") as f:
        raw = tomllib.load(f)

    cfg = Config(
        cowork_root=Path(raw.get("cowork_root", _DEFAULT_COWORK_ROOT)),
        db_path=Path(raw.get("db_path", _DEFAULT_DB_PATH)).expanduser(),
        log_path=Path(raw.get("log_path", _DEFAULT_LOG_PATH)).expanduser(),
        parser=ParserConfig(
            fail_threshold_pct=int(
                raw.get("parser", {}).get("fail_threshold_pct", _DEFAULT_FAIL_THRESHOLD_PCT)
            )
        ),
    )

    # Env-var override for db_path (useful for tests)
    env_db = os.environ.get("COWORK_GRAPH_DB_PATH")
    if env_db:
        cfg.db_path = Path(env_db).expanduser()

    return cfg


def _write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_TOML, encoding="utf-8")
