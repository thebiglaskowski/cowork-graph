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

## Phase status

- **Phase 1** (schema design) — frozen 2026-05-05. No code changes needed.
- **Phase 2** (parser v0) — next. Reads cowork markdown, populates SQLite graph per the frozen schema.
- **Phase 5** (sync hook) — `scripts/install-hook.sh` is a placeholder; real implementation wires a post-commit hook into cowork's `.git/hooks/`.

Do not implement Phase 2+ work without first reading `plan.md` from the cowork side.
