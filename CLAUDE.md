# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Canonical plan

The frozen Phase 1 schema and all design decisions live in the **cowork side** of this pair:
`cowork/claude-environment/cowork-graph/plan.md` (UNC: `\\wsl$\Ubuntu\mnt\c\Users\joela\cowork\claude-environment\cowork-graph\plan.md`).

Before writing parser code, MCP server code, or schema migrations, load that doc. The cowork side wins all design disputes — the WSL repo is the implementation of it, not the source of truth.

## Commands

```bash
# Install (dev dependencies)
uv sync --group dev

# Install with MCP server dependencies
uv sync --group dev --group mcp

# Run CLI
uv run cowork-graph

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/path/to/test_file.py

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

## Architecture

This is a **src-layout** Python package (`src/cowork_graph/`). The CLI entrypoint is `cowork_graph.cli:main`, registered via `pyproject.toml` `[project.scripts]`.

**Dependency groups are intentionally separated:**
- Core (`python-frontmatter`) — markdown parser, always installed
- `dev` — pytest + ruff, local dev only
- `mcp` — `fastmcp`, only needed when running the MCP server; kept optional to keep the base install lean

**Runtime artifacts (`.db`, `.db-journal`, `.db-wal`, `.db-shm`) are gitignored** — the SQLite graph is derived state, rebuilt from cowork markdown on demand. Never commit database files.

**`uv.lock` is committed** — keeps builds reproducible across machines (SKYNET and SKYNET-DUEX both run the same lockfile).

### Key modules

- `cli.py` — command routing; `_write_doc_to_db` is the shared per-doc write helper used by both full build and incremental update.
- `incremental.py` — incremental update logic: `git diff --name-status` parsing, rename rewiring, delete-before-reparse, SQL-based project→entity recompute. Called by `cowork-graph update --since <ref>`.
- `db.py` — schema bootstrap + upsert helpers + `delete_doc` / `rename_doc` for incremental mode.
- `parser.py` / `patterns.py` — markdown frontmatter and link parsing.
- `queries.py` — read-only query layer used by the MCP server tools.
- `audit.py` — ten drift-detection checks; returns structured findings dict.
- `mcp_server.py` — FastMCP server exposing eight tools over stdio.

### CLI commands

```
cowork-graph build                   # Full corpus walk → SQLite graph
cowork-graph update --since HEAD~1   # Incremental re-parse of changed files only
cowork-graph reindex                 # Full rebuild alias (used by git hook on merge commits)
cowork-graph audit [--write]         # Ten drift checks; --write saves markdown report
cowork-graph mcp serve               # Start MCP server over stdio
cowork-graph mcp install             # Register with MCP clients
```

### Sync hook

`scripts/install-hook.sh [/path/to/cowork]` writes a post-commit hook that backgrounds `cowork-graph update --since HEAD~1` after every cowork commit. The CLI detects merge commits and falls back to a full rebuild automatically.

## Phase status

- **Phase 1** (schema design) — complete 2026-05-05.
- **Phase 2** (parser v0) — complete. Full corpus walk populates the SQLite graph.
- **Phase 3** (MCP server v0) — complete. Eight tools, FastMCP, `mcp serve` / `mcp install` CLI.
- **Phase 4** (audit module) — complete. Ten drift checks, CLI subcommand, MCP tool, 48 tests.
- **Phase 5** (sync hook) — complete. `cowork-graph update --since <ref>` for incremental builds; `scripts/install-hook.sh` installs the post-commit hook.
- **Phase 6** (ghost-project fix) — complete. Post-walk pass flips `is_ghost=0` when `memory/projects/<slug>.md` exists.

Do not make schema changes without first reading `plan.md` from the cowork side.
