---
pair:
  cowork: claude-environment/cowork-graph/plan.md
  code: ~/Projects/cowork-graph/
  host: skynet (Omarchy)
  division:
    cowork: planning artifacts, schema reference, audit reports, decision logs
    code: source code (Python parser, MCP server, CLI), tests, dependencies, build artifacts, install scripts
---

![cowork-graph](assets/graph.png)

# cowork-graph

Local SQLite knowledge graph over the entire [`cowork/`](https://github.com/thebiglaskowski/cowork) markdown corpus, plus a semantic search layer over its markdown, PDFs, office files and OCR'd scans, exposed to Claude through one MCP server. Markdown stays canonical; the graph and the search index are derived state and never committed.

**Canonical plan and schema reference:** `cowork/claude-environment/cowork-graph/plan.md` — single source of truth for design decisions, schema, and parser rules. Code-side conventions and runtime usage live here.

## Status

All five build phases shipped (2026-05-05 – 2026-05-06), followed by Phase 6 parser cleanup and a run of hardening work through 2026-07-31 — shared HTTP serve mode, a self-healing watermark, bounded list responses, stateless HTTP transport, and the `jl-graph` Claude Desktop extension. The standing posture is still the passive trust window: fixes as edge cases surface, no new numbered phases planned until nexus-ledger Phase 3 ships.

Measured 2026-08-21:

- 377 tests, 0 failures
- 631 docs parsed from the real corpus, 0 parse failures
- Incremental update: a 75-file daily sync touched 32 docs in **1.8s**; a full rebuild of the same corpus takes **354s**
- Three sync hooks live in `cowork/.git/hooks/` — `post-commit`, `post-merge`, `post-rewrite`
- Served by the `cowork-graph-mcp` systemd user unit on `127.0.0.1:8765`

**Phase 8 (2026-11-07): semantic layer.** Absorbed and retired `cowork-rag-chat` (GitHub `cowork-graph-rag`): hybrid vector + keyword search with a cross-encoder rerank, joined to the graph, plus saved memories — one repo, one server. On the RAG's own 64-question suite it ranks the right source first on 56/56 answerable questions (the RAG managed 55/56), rejects all 8 no-answer questions, at a 198 ms median (RAG: 358 ms). See [Semantic layer](#semantic-layer).

The sync hooks keep the graph current without a full walk: incremental updates keep commits effectively free, and the watermark means a missed run catches up rather than leaving a permanent gap. (On the old WSL host the corpus sat on a Windows mount and a full rebuild took ~354s; on native disk it takes ~13s for 709 docs, measured 2026-11-07.)

## Install

```bash
# Core + dev (tests, lint)
uv sync --group dev

# Core + dev + MCP server
uv sync --group dev --group mcp

# Everything SKYNET runs: MCP server + semantic layer + ingest-time PII detection
uv sync --group dev --group mcp --group semantic --group pii
```

The `mcp` group is kept optional — `fastmcp` is only needed when running the MCP server. `semantic` (FastEmbed, sqlite-vec, PyMuPDF, python-docx, openpyxl) and `pii` (transformers + torch, for the LFM2.5 PII detector) are optional the same way; the graph CLI never imports them. OCR needs the `tesseract` and `tesseract-data-eng` system packages.

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

# MCP server over stdio (default — each client spawns its own process)
uv run cowork-graph mcp serve

# MCP server as a single shared HTTP server (many clients connect remotely)
uv run cowork-graph mcp serve --http [--host 127.0.0.1] [--port 8765] [--path /mcp]

# Register with MCP clients (Claude Desktop, Claude Code WSL, Claude Code PowerShell)
uv run cowork-graph mcp install [--desktop] [--code-wsl] [--code-ps] [--all]

# Semantic layer (needs the `semantic` group)
uv run cowork-graph semantic build            # extract + embed + index the whole corpus
uv run cowork-graph semantic sync [--retry-errors] [--wait]   # changed files only
uv run cowork-graph semantic status [--json]  # counts, failures, models, size
uv run cowork-graph semantic eval [--json]    # parity check vs. cowork-rag-chat's suite
uv run cowork-graph memory import <json>      # one-time import of the RAG's memories

uv run cowork-graph help
```

Config file: `~/.config/cowork-graph/config.toml` (generated on first run with sensible defaults; the `[semantic]` table configures the search layer). Override the DB paths with `COWORK_GRAPH_DB_PATH` and `COWORK_GRAPH_SEMANTIC_DB_PATH`.

## Sync hooks

After building once, wire the git hooks so the graph stays current automatically:

```bash
sh scripts/install-hook.sh [/path/to/cowork]
```

Defaults to `/home/joe/Projects/cowork`. Three hooks get installed, each backgrounding a `cowork-graph update` run (which, once the semantic index exists, also runs a `semantic sync`):

| Hook | `--since` | Covers |
|------|-----------|--------|
| `post-commit` | `HEAD~1` | the commit you just made |
| `post-merge` | `ORIG_HEAD` | fast-forward pulls — the common two-machine sync case |
| `post-rewrite` | `ORIG_HEAD` | rebasing pulls; guarded to `$1 = "rebase"` so `commit --amend` doesn't double-fire |

`post-merge` and `post-rewrite` exist because the graph is derived per-machine and never committed — without them, the machine doing the pulling never sees the other machine's edits until its own next commit, and that commit only sweeps `HEAD~1`, so a multi-commit pull stays permanently missing.

The installer:
- Invokes the project-venv CLI by absolute WSL path, so both a WSL commit (direct) and a Windows commit (routed through `wsl.exe`) resolve it
- Appends to any existing hook (git-lfs safe, idempotent per hook file)
- Backgrounds the update so commits never block

On merge commits the CLI detects the second parent and runs a full rebuild instead of an incremental update. A self-healing last-indexed-SHA watermark means a missed run catches up on the next one rather than leaving a permanent gap.

`.git/hooks` is not tracked by git, so **run this once per clone**. Only SKYNET runs it; SKYNET-DUEX is a remote-access client into SKYNET, not a mirrored install.

## MCP tools

Twelve tools exposed to Claude via the MCP server — eight graph tools and four semantic-layer tools:

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
| `semantic_search` | Hybrid meaning + keyword search over docs, PDFs, office files and OCR'd scans; graph-enriched hits; filters for scope (`-scope` excludes), project, status, kind, path prefix, since |
| `read_document` | Paged extracted text (incl. OCR) of one indexed file, sandboxed to the index |
| `save_memory` | Save a durable note as markdown under `memory/saved/`, indexed immediately |
| `recall_memories` | Search saved memories |

The list-returning tools (`list_active`, `list_blocked`, `decisions`, and `who`'s mentions) accept a `limit` and return a `{items, total, hint}` envelope so a large corpus can't blow up a client's context. `count_only` returns just the count. Defaults are bounded — an unbounded `list_active` against ~500 active docs is what caused 60-second client timeouts before this landed.

## Semantic layer

Plan: `plan.md` "Phase 8 — Semantic layer". Code: `src/cowork_graph/semantic/`.

- **Storage.** `~/.local/share/cowork-graph/semantic.db`, separate from `graph.db` so the frozen graph schema and the 13s `reindex` never touch embeddings. Vectors live in a `sqlite-vec` `vec0` table (768-d cosine), keyword search in FTS5, extracted text in `file_text` (so OCR runs once), PII records in `pii`. One writer at a time (a lock file); the MCP server reads under WAL. Pure derived state — delete it and run `semantic build` to rebuild.
- **What's indexed.** Every `.md`, `.txt`, `.csv`, `.pdf`, `.docx`, `.xlsx` and image (`.jpg`, `.png`, `.webp`, `.tif`, `.bmp`) under the cowork root, skipping `.git`, `.obsidian`, `_archive`, `_backups`, `_to_delete`, `.claude` and hidden dirs. Markdown is split on headings (each chunk carries an `H1 > H2` breadcrumb); PDF pages with under 30 characters of text are OCR'd with Tesseract at 200 DPI; image OCR text is kept only when it reads as writing.
- **Retrieval.** Nomic `nomic-embed-text-v1.5` (Nomic task prefixes) and the `ms-marco-MiniLM-L-6-v2` cross-encoder, both FastEmbed on CPU. Vector top-200 and BM25 top-200 → filters → reciprocal-rank fusion → rerank of the top 40 → max 3 passages per file → a capped recency bonus. "Recency" uses each file's last git commit (a fresh clone resets every mtime), falling back to mtime for untracked files.
- **Freshness.** `semantic sync` stat-scans the corpus (well under a second), md5-confirms changes, and re-embeds only real changes — tracked or gitignored. The git hooks run it after every graph update. A file that fails extraction records its error and is retried only when it changes, or by the Sunday `--retry-errors` sweep.
- **PII.** Ported unchanged from cowork-rag-chat: the LFM2.5 detector records identifiers at ingest (`[semantic] pii_detect`, `pii_device`); tools mask them at serve time as `[pii …last4]`. Names, companies and dates stay readable.
- **Memories.** `save_memory` writes `memory/saved/YYYY-MM-DD-<slug>.md` (tags `status/reference`, `type/memory`) into the cowork corpus and indexes it at once. Notes containing raw identifiers are refused. The files are left uncommitted; they reach the other machine with the next cowork commit.

## Shared HTTP server

By default each MCP client spawns its **own** `cowork-graph mcp serve` process over stdio. When several clients run at once (Claude Code, OpenCode, Hermes), those per-client stdio spawns can contend on startup — a slow/blocked spawn can hang the client's MCP init. To avoid that, run **one** shared server over HTTP and have every client connect to it remotely:

```bash
cowork-graph mcp serve --http            # serves http://127.0.0.1:8765/mcp
```

Point any MCP client at the URL via a remote/HTTP transport. Example for OpenCode (`~/.config/opencode/opencode.json`):

```json
"mcp": {
  "cowork-graph": { "type": "remote", "url": "http://127.0.0.1:8765/mcp", "enabled": true }
}
```

Keep it running as a systemd **user** service:

```ini
# ~/.config/systemd/user/cowork-graph-mcp.service
[Unit]
Description=cowork-graph MCP server (shared HTTP)
After=network.target

[Service]
ExecStart=%h/Projects/cowork-graph/.venv/bin/cowork-graph mcp serve --http
Restart=on-failure
# A SIGTERM stop exits 143; without this every clean stop logs as "Failed".
SuccessExitStatus=143

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now cowork-graph-mcp
loginctl enable-linger "$USER"           # survive logout/reboot
```

`stdio` remains the default transport; `--http` is purely additive, so existing stdio installs (`mcp install`) are unaffected.

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
├── mcp_server.py    # FastMCP server (12 tools)
├── semantic/        # Phase 8: discover, extract, chunking, models, pii, store, sync, search, memory, evaluate, cli
├── config.py        # Config file load/generate
├── install.py       # MCP client registration (Desktop, Code WSL, Code PS)
└── schema.sql       # SQLite schema (FTS5, WAL, foreign keys)
```

**src-layout** Python package. CLI registered via `pyproject.toml` `[project.scripts]`.

**Dependency groups are intentionally separated** — `fastmcp` is optional so the base install stays lean for machines that only need the CLI.

**Database files are gitignored** (`.db`, `.db-journal`, `.db-wal`, `.db-shm`) — the graph is derived state, rebuilt from cowork markdown on demand. Never commit it.

**`uv.lock` is committed** — reproducible builds on any clone.

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
