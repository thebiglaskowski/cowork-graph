"""Multi-surface MCP client registration for cowork-graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_SERVER_NAME = "cowork-graph"
_WSL_REPO = "/home/joe/github/cowork-graph"

# Canonical config-file paths per surface
_CC_WSL_CONFIG = Path.home() / ".claude.json"
_DESKTOP_CONFIG = Path(
    "/mnt/c/Users/joela/AppData/Roaming/Claude/claude_desktop_config.json"
)
_CC_PS_CONFIG = Path("/mnt/c/Users/joela/.claude.json")


@dataclass
class InstallResult:
    surface: str
    status: str          # 'installed' | 'updated' | 'skipped'
    reason: str | None = None


def _cc_wsl_entry() -> dict:
    return {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--directory", _WSL_REPO, "cowork-graph", "mcp", "serve"],
        "env": {},
    }


def _desktop_entry() -> dict:
    # Desktop runs on Windows — invokes WSL. No 'type' field (Claude Desktop rejects it).
    # bash -lc is required: wsl -e runs execvpe() directly (no login shell, no .profile),
    # so ~/.local/bin (where uv lives) is not on PATH without the -l flag.
    return {
        "command": "wsl",
        "args": ["-e", "bash", "-lc", f"uv run --directory {_WSL_REPO} cowork-graph mcp serve"],
        "env": {},
    }


def _cc_ps_entry() -> dict:
    # Same bash -lc requirement as desktop entry (cross-context WSL invocation).
    return {
        "type": "stdio",
        "command": "wsl",
        "args": ["-e", "bash", "-lc", f"uv run --directory {_WSL_REPO} cowork-graph mcp serve"],
        "env": {},
    }


def _install_to(config_path: Path, entry: dict, surface: str) -> InstallResult:
    if not config_path.exists():
        return InstallResult(surface=surface, status="skipped", reason="config not found")

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return InstallResult(surface=surface, status="skipped", reason=f"unreadable: {exc}")

    servers = data.setdefault("mcpServers", {})
    is_update = _SERVER_NAME in servers
    servers[_SERVER_NAME] = entry
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return InstallResult(surface=surface, status="updated" if is_update else "installed")


def install(
    *,
    desktop: bool = False,
    code_wsl: bool = False,
    code_ps: bool = False,
) -> list[InstallResult]:
    results: list[InstallResult] = []
    if code_wsl:
        results.append(_install_to(_CC_WSL_CONFIG, _cc_wsl_entry(), "code-wsl"))
    if desktop:
        results.append(_install_to(_DESKTOP_CONFIG, _desktop_entry(), "desktop"))
    if code_ps:
        results.append(_install_to(_CC_PS_CONFIG, _cc_ps_entry(), "code-ps"))
    return results


def print_results(results: list[InstallResult]) -> None:
    for r in results:
        if r.status == "skipped":
            print(f"[skipped: {r.reason}] {r.surface}")
        else:
            print(f"[{r.status}] {r.surface}")
