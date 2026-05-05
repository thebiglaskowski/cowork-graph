---
tags:
  - status/active
  - project/cowork-graph
  - type/reference
---

# WSL repo scaffolding brief

> **Related:** [plan](plan.md) — canonical Phase 1 schema and roadmap. This brief is a one-time handoff to be pasted into a Claude Code session running at `/home/joe/github/cowork-graph/`. Lives in cowork as a reproducibility artifact for second-machine setup.

Paste the section below this line into Claude Code. Everything above this line is the cowork-side meta wrapper — skip it.

---

# Brief — scaffold cowork-graph WSL repo

**Paste into Claude Code at `/home/joe/github/cowork-graph/`.**

You're scaffolding the cowork-graph repo. The canonical plan and frozen Phase 1 schema live in cowork at `claude-environment/cowork-graph/plan.md` (UNC: `\\wsl$\Ubuntu\mnt\c\Users\joela\cowork\claude-environment\cowork-graph\plan.md`). For this scaffolding session you don't need to load that plan — just create the structure described below exactly. Phase 2 (actual parser code) is a separate session against the frozen schema.

The cowork ↔ WSL pair convention requires a reciprocal `pair:` frontmatter block in this repo's README, mirroring the one in `plan.md`. Don't change the structure of the pair block — it's load-bearing.

## Files to create

### `README.md`

````markdown
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
````

### `.gitignore`

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
.venv/
venv/
env/

# Editor / IDE
.vscode/
.idea/
*.swp
*.swo
.DS_Store

# cowork-graph runtime artifacts (per-machine, never committed — markdown is canonical)
*.db
*.db-journal
*.db-wal
*.db-shm

# Logs
*.log

# Pytest / coverage
.pytest_cache/
.coverage
htmlcov/
```

### `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "cowork-graph"
version = "0.1.0"
description = "Local knowledge graph over the cowork markdown corpus"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Joe Laskowski" }]
dependencies = [
  "python-frontmatter>=1.0",
]

[project.scripts]
cowork-graph = "cowork_graph.cli:main"

[dependency-groups]
dev = [
  "pytest>=8",
  "ruff>=0.4",
]
mcp = [
  "fastmcp>=0.1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/cowork_graph"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

### `src/cowork_graph/__init__.py`

```python
"""cowork-graph — local knowledge graph over the cowork markdown corpus."""

__version__ = "0.1.0"
```

### `src/cowork_graph/cli.py`

```python
"""CLI entry point. Real commands land in Phase 2."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "cowork-graph CLI — Phase 1 scaffolding only. "
        "Real commands land in Phase 2."
    )
    print(
        "See cowork/claude-environment/cowork-graph/plan.md "
        "for the schema and roadmap."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### `tests/__init__.py`

```python
# Placeholder. Real tests land alongside Phase 2 parser code.
```

### `scripts/install-hook.sh`

```bash
#!/bin/sh
# Placeholder — real implementation lands in Phase 5 (sync mechanism).
# When complete, this script will:
#   1. Detect the cowork repo location (default: /mnt/c/Users/joela/cowork)
#   2. Copy the post-commit hook to <cowork>/.git/hooks/post-commit
#   3. Make it executable
#   4. Verify cowork-graph CLI is on PATH
echo "install-hook.sh — placeholder. Phase 5 work."
exit 0
```

### `LICENSE`

```
MIT License

Copyright (c) 2026 Joe Laskowski

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USAGE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## After files exist

```bash
chmod +x scripts/install-hook.sh
git init -b main
git add .
git commit -m "scaffold cowork-graph (Phase 1 deliverable, schema frozen)"
```

Optional sanity check:

```bash
uv sync --group dev
uv run cowork-graph
# Should print the Phase 1 placeholder message and exit 0.
# uv creates .venv automatically; uv.lock is created and SHOULD be committed (not gitignored).
```

## Do NOT

- Write parser code in this session — that's Phase 2 against the frozen schema in `plan.md`.
- Create `~/.config/cowork-graph/config.toml` here — that's per-machine runtime config, not a repo artifact.
- Wire the post-commit hook into cowork's `.git/hooks/` — Phase 5 deliverable.
- Touch the cowork repo or any markdown files outside this WSL directory.
- Add dependencies beyond what's listed — the plan pins the dependency surface tight on purpose.
