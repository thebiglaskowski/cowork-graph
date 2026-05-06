---
pair:
  cowork: claude-environment/cowork-graph/plan.md
  wsl: ~/github/cowork-graph/
  unc: \\wsl$\Ubuntu\home\joe\github\cowork-graph\
  division:
    cowork: planning artifacts, schema reference, audit reports, decision logs
    wsl: source code (Python parser, MCP server, CLI), tests, dependencies, build artifacts, install scripts
---

# cowork-graph

Local SQLite knowledge graph over the entire `cowork/` markdown corpus, exposed to Claude Desktop, Claude Code WSL, and Claude Code PowerShell through a custom MCP server. Markdown stays canonical; the graph is derived state and never committed.

**Canonical plan and schema reference:** `cowork/claude-environment/cowork-graph/plan.md` — single source of truth for design decisions, schema, and parser rules. Code-side conventions and runtime usage live here.

## Status

All five build phases shipped (2026-05-05 – 2026-05-06). Now in Phase 6: passive trust window — in-flight fixes as edge cases surface, no planned new phases until nexus-ledger Phase 3 ships.

- 282 tests, 0 failures
- 216 docs parsed from real corpus, 0 parse failures
- Incremental update: ~0.4s for a 2-file commit vs ~24s full rebuild
- Post-commit hook live in `cowork/.git/hooks/post-commit`

## Install

```bash
# Core + dev (tests, lint)
uv sync --group dev

# Core + dev + MCP server
uv sync --group dev --group mcp
```

The `mcp` group is kept optional — `fastmcp` is only needed when running the MCP server.

## CLI

```bash
# Full corpus walk → SQLite graph
uv run cowork-graph build

# Incremental re-parse of files changed since a git ref
uv run cowork-graph update --since HEAD~1

# Full rebuild alias (used by the git hook on merge commits)
uv run cowork-graph reindex

# Ten drift-detection checks; --write saves a markdown report to cowork audits dir
uv run cowork-graph audit [--write]

# MCP server over stdio
uv run cowork-graph mcp serve

# Register with MCP clients (Claude Desktop, Claude Code WSL, Claude Code PowerShell)
uv run cowork-graph mcp install [--desktop] [--code-wsl] [--code-ps] [--all]

uv run cowork-graph help
```

Config file: `~/.config/cowork-graph/config.toml` (generated on first run with sensible defaults). Override the DB path with `COWORK_GRAPH_DB_PATH`.

## Sync hook

After building once, wire the post-commit hook so the graph stays current automatically:

```bash
sh scripts/install-hook.sh [/path/to/cowork]
```

Defaults to `/mnt/c/Users/joela/cowork`. The installer:
- Resolves the CLI path (PATH first, falls back to `.venv/bin/cowork-graph`)
- Appends to any existing hook (git-lfs safe, idempotent)
- Writes `cowork-graph update --since HEAD~1 &` (backgrounded)

On merge commits the CLI detects the second parent and runs a full rebuild instead of an incremental update.

## MCP tools

Eight tools exposed to Claude via the MCP server:

| Tool | Description |
|------|-------------|
| `search_docs` | FTS5 full-text search over all docs |
| `get_doc` | Fetch metadata for a doc by path |
| `list_active` | All docs with `status=active` |
| `list_blocked` | Docs with outbound BLOCKS edges |
| `project_state` | Hub doc, member docs, entity, ghost status for a project |
| `who` | Person lookup by name or alias |
| `decisions` | Decision log entries with optional date/status filter |
| `audit` | Run all ten drift checks; optional `write_report` flag |

## Audit checks

`cowork-graph audit` runs ten checks and returns a JSON summary:

- `ghost_projects` — projects with a `project/<slug>` tag but no hub doc
- `ghost_people` — people mentioned but no `memory/people/<slug>.md`
- `broken_links` — internal markdown links that resolve to nonexistent paths
- `one_way_edges` — LINKS_TO edges with no reciprocal link
- `stale_active_docs` — `status=active` docs not modified in >90 days
- `inconsistent_hub_state` — hub docs whose project row is still marked ghost
- `orphan_docs` — docs with no inbound or outbound edges
- `decision_drift` — decisions whose source doc no longer exists
- `tag_drift` — tags on docs that don't match the entity-scoped tag set
- `decisions_format_drift` — decision entries missing required fields

## Architecture

```
src/cowork_graph/
├── cli.py           # Command routing; _write_doc_to_db shared by build + incremental
├── incremental.py   # git diff parsing, rename rewiring, delete-before-reparse, SQL project→entity recompute
├── db.py            # Schema bootstrap, upsert helpers, delete_doc, rename_doc
├── parser.py        # Frontmatter, link, mention, decision-log parsing
├── patterns.py      # Compiled regex patterns (RE_MD_LINK, RE_DECISION_HEADING, etc.)
├── walker.py        # Corpus walk (skips _archive/, .obsidian/)
├── queries.py       # Read-only query layer for the MCP server
├── audit.py         # Ten drift-detection checks
├── mcp_server.py    # FastMCP server (8 tools)
├── config.py        # Config file load/generate
├── install.py       # MCP client registration (Desktop, Code WSL, Code PS)
└── schema.sql       # SQLite schema (FTS5, WAL, foreign keys)
```

**src-layout** Python package. CLI registered via `pyproject.toml` `[project.scripts]`.

**Dependency groups are intentionally separated** — `fastmcp` is optional so the base install stays lean for machines that only need the CLI.

**Database files are gitignored** (`.db`, `.db-journal`, `.db-wal`, `.db-shm`) — the graph is derived state, rebuilt from cowork markdown on demand. Never commit it.

**`uv.lock` is committed** — reproducible builds across SKYNET and SKYNET-DUEX.

## Development

```bash
uv run pytest                              # Full suite
uv run pytest tests/test_db.py -x -q      # Single file
uv run ruff check src/ tests/              # Lint
uv run ruff format src/ tests/             # Format
```

## Related

- Plan + schema: `cowork/claude-environment/cowork-graph/plan.md`
- Audit reports: `cowork/claude-environment/cowork-graph/audits/`
- Sibling pattern: `cowork/nexus-ledger/` (same cowork ↔ WSL split)
