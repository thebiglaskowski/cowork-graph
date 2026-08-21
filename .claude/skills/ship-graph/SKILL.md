---
name: ship-graph
description: Test, lint, restart the cowork-graph-mcp systemd unit, and smoke-test the live HTTP endpoint after server-side changes. Run this whenever anything under src/cowork_graph/ changed and the running server needs to pick it up.
disable-model-invocation: true
---

# ship-graph

Deploys code changes to the running cowork-graph MCP server and proves they landed.

## Why this exists

The server runs as a **long-lived systemd user unit**. Editing `src/cowork_graph/`, passing all 377 tests, and committing cleanly does **not** ship anything — the process on :8765 keeps serving the code it was started with. Green tests plus a clean commit reads as success while the live server is stale. That's the expensive kind of failure: the one that looks like the good outcome.

This skill is the ritual that closes the gap, and it reports every step's real output.

## Topology

```
Claude Desktop / Cowork / artifacts ─ jl-graph ext (mcp-remote) ─┐
                                                                 ├─→ 127.0.0.1:8765/mcp
Claude Code (WSL/PowerShell), OpenCode, Hermes ──────────────────┘   cowork-graph-mcp.service
```

Restarting is safe for connected clients: the HTTP transport runs `stateless_http=True` (commit 1002d4b) precisely so a restart doesn't strand sessions with session-not-found 404s.

## Steps

Run these in order **from `~/github/cowork-graph`**. Report each step's actual output. If any step fails, **stop and report** — do not continue to the next step, and do not describe the deploy as done.

### 1. Tests

```bash
uv run pytest -q
```

All must pass. A skipped or deselected test is not a passing test — say so if the count differs from expectation.

### 2. Lint

```bash
uv run ruff check src/ tests/
```

### 3. Restart the service

```bash
systemctl --user restart cowork-graph-mcp.service
systemctl --user show -p ActiveState -p NRestarts --value cowork-graph-mcp.service
```

`ActiveState` must be `active`. A climbing `NRestarts` means a crash loop — the unit is restarting itself, not serving. Report the number.

### 4. Smoke-test the live endpoint

```bash
curl -s -m 15 -o /tmp/mcp_smoke.txt -w "HTTP %{http_code}\n" \
  -X POST http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

sed -n 's/^data: //p' /tmp/mcp_smoke.txt | python3 -c "
import json,sys
d=json.load(sys.stdin)
names=[t['name'] for t in d['result']['tools']]
print(f'{len(names)} tools:', ', '.join(names))
"
```

Expect `HTTP 200` and **8 tools**: `search_docs`, `get_doc`, `list_active`, `list_blocked`, `project_state`, `who`, `decisions`, `audit`.

The response is SSE-framed (`event: message` / `data: {...}`) — that's why the body is parsed through `sed -n 's/^data: //p'` rather than piped straight to a JSON parser.

If the tool count changed, that's expected only when this deploy added or removed a tool — and in that case `mcpb/manifest.json` needs the matching update and a version bump. Flag it.

### 5. Exercise one real call

```bash
curl -s -m 30 -X POST http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_active","arguments":{"count_only":true}}}' \
  | sed -n 's/^data: //p' | head -c 400
```

`count_only` keeps this cheap — the corpus has ~498 active docs and unbounded list calls are what caused the S948 timeouts. A response here proves the DB is readable and the query layer works, not just that the process is up.

## Stop here

**Do not commit or push.** Run `git status` and show Joe the full diff.

If the working tree contains changes outside this task's scope — files you didn't author this session — **stop and surface them**. A parallel Claude Code session or Hermes may be active in this repo, and committing over another agent's work is the thing that rule exists to prevent. Note that `uv.lock` in particular has carried uncommitted changes across sessions before.

## Report

State plainly for each step: ran / passed / failed, with the real numbers — test count, `NRestarts`, HTTP status, tool count. If you skipped a step, say which and why. "Deployed" is wrong if step 4 didn't return 200.
