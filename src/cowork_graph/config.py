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

_DEFAULT_COWORK_ROOT = "/home/joe/Projects/cowork"
_DEFAULT_DB_PATH = "~/.local/share/cowork-graph/graph.db"
_DEFAULT_LOG_PATH = "~/.local/share/cowork-graph/parse.log"
_DEFAULT_FAIL_THRESHOLD_PCT = 5
_DEFAULT_SEMANTIC_DB_PATH = "~/.local/share/cowork-graph/semantic.db"
_DEFAULT_MODEL_CACHE = "~/.cache/fastembed"
# Ported verbatim from cowork-rag-chat's PII_MASK_LABELS default (its CLAUDE.md
# invariant 9): identifiers get masked, names stay servable.
DEFAULT_PII_MASK_LABELS = (
    "financial",
    "credential",
    "contact",
    "healthcare",
    "device",
    "special",
    "identity.ssn",
    "identity.passport",
    "identity.national_id",
    "identity.driver",
)

_DEFAULT_TOML = """\
# cowork-graph per-machine configuration
# Generated on first run. Edit as needed.

# Absolute path to the root of the cowork repository on this machine.
# cowork_root = "/home/joe/Projects/cowork"

# Path to the SQLite graph database. ~ is expanded.
# db_path = "~/.local/share/cowork-graph/graph.db"

# Path to the parse log. ~ is expanded.
# log_path = "~/.local/share/cowork-graph/parse.log"

[parser]
# Exit non-zero from `cowork-graph build` if more than this percentage
# of docs fail to parse.
# fail_threshold_pct = 5

[semantic]
# Phase 8 semantic layer (needs the `semantic` dependency group).
# db_path = "~/.local/share/cowork-graph/semantic.db"
# model_cache = "~/.cache/fastembed"
# OCR scanned PDF pages and images with Tesseract.
# ocr = true
# Parallel extraction workers (OCR is the slow part).
# workers = 6
# PII detection at ingest: "auto" (when the `pii` group is installed), "on", "off".
# pii_detect = "auto"
# pii_device = "cpu"
# Where save_memory writes notes, relative to cowork_root.
# memory_dir = "memory/saved"
"""


@dataclass
class ParserConfig:
    fail_threshold_pct: int = _DEFAULT_FAIL_THRESHOLD_PCT


@dataclass
class SemanticConfig:
    db_path: Path = field(default_factory=lambda: Path(_DEFAULT_SEMANTIC_DB_PATH).expanduser())
    model_cache: Path = field(default_factory=lambda: Path(_DEFAULT_MODEL_CACHE).expanduser())
    ocr: bool = True
    workers: int = 6
    pii_detect: str = "auto"
    pii_device: str = "cpu"
    pii_mask_labels: tuple[str, ...] = DEFAULT_PII_MASK_LABELS
    memory_dir: str = "memory/saved"


@dataclass
class Config:
    cowork_root: Path = field(default_factory=lambda: Path(_DEFAULT_COWORK_ROOT))
    db_path: Path = field(default_factory=lambda: Path(_DEFAULT_DB_PATH).expanduser())
    log_path: Path = field(default_factory=lambda: Path(_DEFAULT_LOG_PATH).expanduser())
    parser: ParserConfig = field(default_factory=ParserConfig)
    semantic: SemanticConfig = field(default_factory=SemanticConfig)

    @property
    def suppressions_path(self) -> Path:
        return self.cowork_root / "claude-environment/cowork-graph/audits/suppressions.md"


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
        semantic=_load_semantic(raw.get("semantic", {})),
    )

    # Env-var override for db_path (useful for tests)
    env_db = os.environ.get("COWORK_GRAPH_DB_PATH")
    if env_db:
        cfg.db_path = Path(env_db).expanduser()

    return cfg


def _load_semantic(raw: dict) -> SemanticConfig:
    pii_detect = str(raw.get("pii_detect", "auto")).lower()
    if pii_detect not in ("auto", "on", "off"):
        # Fail loud: a typo here must not silently disable PII detection.
        raise ValueError(f"[semantic] pii_detect must be auto, on, or off; got {pii_detect!r}")
    labels = raw.get("pii_mask_labels", DEFAULT_PII_MASK_LABELS)
    cfg = SemanticConfig(
        db_path=Path(raw.get("db_path", _DEFAULT_SEMANTIC_DB_PATH)).expanduser(),
        model_cache=Path(raw.get("model_cache", _DEFAULT_MODEL_CACHE)).expanduser(),
        ocr=bool(raw.get("ocr", True)),
        workers=max(1, int(raw.get("workers", 6))),
        pii_detect=pii_detect,
        pii_device=str(raw.get("pii_device", "cpu")),
        pii_mask_labels=tuple(str(x) for x in labels),
        memory_dir=str(raw.get("memory_dir", "memory/saved")).strip("/"),
    )
    env_db = os.environ.get("COWORK_GRAPH_SEMANTIC_DB_PATH")
    if env_db:
        cfg.db_path = Path(env_db).expanduser()
    return cfg


def _write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_TOML, encoding="utf-8")
