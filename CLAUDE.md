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
- `audit_html.py` — HTML renderer for audit reports (a view over the markdown report, never a replacement).
- `suppressions.py` — `(rule, key)` allowlist for intentional findings, read from the cowork-side audits dir.
- `mcp_server.py` — FastMCP server exposing eight tools over stdio or shared HTTP.

### CLI commands

```
cowork-graph build                   # Full corpus walk → SQLite graph
cowork-graph update --since HEAD~1   # Incremental re-parse of changed files only
cowork-graph reindex                 # Full rebuild alias (used by git hook on merge commits)
cowork-graph audit [--write]         # Ten drift checks; --write saves markdown report
cowork-graph mcp serve               # Start MCP server over stdio
cowork-graph mcp serve --http        # Shared HTTP server (default 127.0.0.1:8765/mcp)
cowork-graph mcp install             # Register with MCP clients
```

### Sync hooks

`scripts/install-hook.sh [/path/to/cowork]` installs **three** hooks into the cowork repo, each backgrounding a `cowork-graph update` run:

| Hook | `--since` | Covers |
| --- | --- | --- |
| `post-commit` | `HEAD~1` | the commit just made |
| `post-merge` | `ORIG_HEAD` | fast-forward pulls (the common two-machine sync) |
| `post-rewrite` | `ORIG_HEAD` | rebasing pulls; guarded to `$1 = "rebase"` |

The CLI detects merge commits and falls back to a full rebuild automatically. A self-healing last-indexed-SHA watermark means a missed run catches up on the next one rather than leaving a permanent gap.

`.git/hooks` is not tracked by git, so this must be run **once per machine** — SKYNET and SKYNET-DUEX each need their own install.

### Deployment

The MCP server runs as a systemd user unit, `cowork-graph-mcp.service`, serving shared HTTP on `127.0.0.1:8765/mcp`. Claude Desktop and Cowork reach it through the `jl-graph` mcpb extension (`mcpb/`), a thin `mcp-remote` stdio→HTTP proxy — the extension bundles no Python and no fastmcp.

**Editing `src/cowork_graph/` does not ship anything.** The running unit keeps serving the code it started with until `systemctl --user restart cowork-graph-mcp`. Green tests and a clean commit are not evidence the change is live. Use `/ship-graph`, which restarts and smoke-tests the endpoint.

The HTTP transport runs `stateless_http=True` so restarts don't strand connected clients with session-not-found errors.

### Claude Code tooling (`.claude/`)

Committed and portable:

- **Skills** — `/ship-graph` (test → lint → restart the unit → smoke-test :8765) and `/add-audit-check` (wires a new drift check through all nine registration points). Both are user-invocable only.
- **Agents** — `mcp-contract-reviewer` (tool-set drift between `mcp_server.py` and `mcpb/manifest.json`), `graph-invariant-reviewer` (full-rebuild vs incremental divergence), `query-perf-analyzer` (N+1 fan-out in `queries.py`), `audit-triage` (findings → fix-corpus / fix-parser / suppress), `doc-pair-sync-reviewer` (doc drift across the WSL/cowork pair), `hook-integrity-checker` (installed sync hooks vs what `install-hook.sh` emits).
- **Hooks** — `schema-guard.sh` (PreToolUse; blocks `schema.sql` edits) and `restart-reminder.sh` (Stop; advisory when the running unit is older than `src/`).

**Hook registration is per-machine.** The scripts are committed, but they are wired up in `.claude/settings.local.json`, which is gitignored because it carries absolute paths. On a fresh clone the hooks exist but are inert until registered there — same once-per-machine discipline as the git sync hooks.

## Phase status

- **Phase 1** (schema design) — complete 2026-05-05.
- **Phase 2** (parser v0) — complete. Full corpus walk populates the SQLite graph.
- **Phase 3** (MCP server v0) — complete. Eight tools, FastMCP, `mcp serve` / `mcp install` CLI.
- **Phase 4** (audit module) — complete. Ten drift checks, CLI subcommand, MCP tool, 48 tests.
- **Phase 4.5** (suppressions) — complete 2026-05-06. `(rule, key)` allowlist in `suppressions.py`, read from the cowork-side audits dir, so intentional findings stop counting as drift.
- **Phase 5** (sync hook) — complete. `cowork-graph update --since <ref>` for incremental builds; `scripts/install-hook.sh` installs the post-commit hook.
- **Phase 6** (parser cleanup Round 1) — complete. Four fixes: link-with-title regex, path normalization (`posixpath.normpath`), ABOUT_DECISION broadened to all decision fields, ghost-project resolution via hub-doc TAGGED-edge query. Baseline audit 2026-05-06: 195 total findings (from 170 on stale DB; corpus grew during session). ghost_projects: 8→4.
- **Audit HTML reports** — complete 2026-05-09. `audit_html.py`: dark theme, SVG count bars, relpath links. `--write` emits HTML by default alongside the markdown report. The HTML is a regenerable view; the markdown stays canonical.

### Post-Phase-6 hardening (2026-05-25 – 2026-07-31)

Shipped during the passive trust window, outside the numbered phases:

- **Cross-surface hooks** (69993f6, 6696bf2) — the cowork repo is committed from both WSL git and Windows git, so hook blocks route through `wsl.exe` when `/home/joe/...` won't resolve. Later broadened from one hook to three (`post-commit`, `post-merge`, `post-rewrite`) and the idempotency guard fixed — it had grepped for a string the block never emitted, so re-running double-appended.
- **Shared HTTP serve mode** (#1, 70717a9) — `mcp serve --http`, one server for many clients instead of a process per client.
- **Self-healing watermark** (7db86da) — last-indexed-SHA in `schema_meta`; a missed or failed run leaves it unadvanced so the next run covers the gap.
- **Bounded list responses** (15985f5) — `limit` / `count_only` on every list tool plus a `_bounded()` envelope, after ~498 active docs caused 60-second `list_active` timeouts.
- **Stateless HTTP transport** (1002d4b) — `stateless_http=True`, so restarting the service no longer strands clients in a session-not-found loop.
- **jl-graph mcpb extension** (b1ec9f7) — Claude Desktop extension source. Renamed from `cowork-graph` because Desktop reserves the `cowork` prefix and silently drops colliding extensions; the tool prefix clients see is now `mcp__jl-graph__*`.

Do not make schema changes without first reading `plan.md` from the cowork side. A `PreToolUse` hook in `.claude/hooks/schema-guard.sh` blocks edits to `schema.sql` to enforce this.
