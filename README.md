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

**Canonical plan and schema reference:** `cowork/claude-environment/cowork-graph/plan.md` — that doc is the single source of truth for design decisions, schema, parser rules, and phasing. Code-side conventions and runtime usage docs live in this repo.

## Status

Phase 1 (schema design) frozen as of 2026-05-05. Phase 2 (parser v0) is the next work.

## Layout

```
cowork-graph/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── src/
│   └── cowork_graph/
│       ├── __init__.py
│       └── cli.py
├── tests/
│   └── __init__.py
└── scripts/
    └── install-hook.sh        (placeholder — real implementation lands in Phase 5)
```

## Install

```bash
uv sync --group dev
```

## Related

- Plan + schema: `cowork/claude-environment/cowork-graph/plan.md`
- Sibling pattern: `cowork/nexus-ledger/` (same cowork ↔ WSL split)
