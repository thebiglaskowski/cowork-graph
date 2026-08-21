---
name: add-audit-check
description: Add a new drift-detection check to the cowork-graph audit module, wiring all nine registration points so the check runs, renders in both markdown and HTML reports, and can be suppressed. Use when adding an eleventh (or later) audit check.
disable-model-invocation: true
---

# add-audit-check

Adds one drift check to `audit.py` and wires it everywhere it must be registered.

## Why a skill and not just an edit

A check is registered in **nine** places across four files. Ten checks in, the pattern is stable — and so are the ways it goes wrong:

- Miss the `raw` dict → the check never runs, and nothing errors.
- Miss `_format_finding` → the markdown report prints a bare `{'slug': 'x'}` dict.
- Miss `_SEVERITY` / `_finding_content` in `audit_html.py` → the HTML report degrades or omits the section.
- **Miss `_make_key` in `suppressions.py` → `ValueError: Unknown rule` is raised the first time anyone tries to suppress anything, taking down the whole audit run.**

That last one is the dangerous one: it's the only registration point that raises rather than degrading, and it fires on a code path (suppression) that isn't exercised until someone actually uses it.

## Before you start

Check whether the finding is genuinely a **corpus** drift signal and not a **parser** bug. All of Phase 6 was audit checks firing on parser faults, not real drift. A check that reports a parser bug as corpus drift sends Joe to edit markdown that was never wrong. If in doubt, run `graph-invariant-reviewer` first.

Also decide up front: **what does a clean corpus look like for this check?** A check that can never reach zero is a permanent noise generator. `tag_drift` is already the noisiest check for exactly this reason (it fires on every legitimately-rare tag); don't add a second one.

## The nine registration points

### `src/cowork_graph/audit.py`

**1. The check function.** Module-level, takes `conn: sqlite3.Connection`, returns `list[dict]`, one-line docstring stating the rule in corpus terms. Tunable thresholds are keyword-only with a default (see `stale_active_docs(conn, *, days=90)`).

```python
def my_new_check(conn: sqlite3.Connection) -> list[dict]:
    """One sentence describing the drift, in corpus terms not SQL terms."""
    rows = conn.execute("SELECT ...").fetchall()
    return [{"path": r["path"]} for r in rows]
```

Keep the finding dict flat and stable — its keys become the suppression key and both report renderers.

**2. The `raw` dict in `run_audit`.** Add the entry. If the check takes a threshold, thread it through as a keyword-only parameter on `run_audit` alongside `stale_days` / `ghost_min_mentions` / `tag_max_count`.

**3. `_SECTION_LABELS`** — human-readable title for the markdown report.

**4. `_format_finding`** — a branch returning a one-line string. Match the house style: backticked paths, em-dash before the explanation.

### `src/cowork_graph/audit_html.py`

**5. `_SEVERITY`** — one of `high` / `medium` / `low` / `info`. Be honest; inflating severity trains the reader to ignore the report.

**6. `_SECTION_LABELS`** — same label as point 3. These are two separate dicts that must agree.

**7. `_finding_content`** — returns `(title_html, body_html, source_path_or_None)`. **Escape every interpolated value** with `_html.escape` — corpus content is arbitrary markdown text. Return the source path when the finding points at one doc so the report can link it.

### `src/cowork_graph/suppressions.py`

**8. `_make_key`** — add the branch **before** the `else: raise ValueError`. Choose a key that is stable across rebuilds: a path or slug, never a row id or count. For two-sided findings use the `a::b` convention already in use (`source_doc::link_target`, `doc_a::doc_b`, `hub::member`).

### Tests

**9.** Cover all of it:
- `tests/test_audit.py` — the check function against fixtures: at least one finding case and one clean case.
- `tests/test_audit_html.py` — the finding renders, and content is HTML-escaped.
- `tests/test_suppressions.py` — `_make_key` returns the expected key and the finding actually suppresses.

Fixtures live in `tests/fixtures/corpus/`. Extend that corpus rather than mocking the DB — these checks are only meaningful against a real parsed graph.

## Then: the count is hardcoded in six files

"Ten" appears in prose and docstrings that all become wrong at once:

```bash
grep -rn "ten drift\|ten checks\|Ten drift\|all ten" src/ README.md CLAUDE.md
```

Currently: `audit.py` (module docstring + `run_audit` docstring), `cli.py` (help text), `mcp_server.py` (the `audit` tool description — **this one is user-facing through the MCP client**), `queries.py`, `CLAUDE.md` (×3), `README.md` (×2).

The `mcp_server.py` docstring matters most: it's what Claude reads to decide when to call the tool, and a stale count there is a small lie shipped to every client.

## Verify

```bash
uv run pytest -q
uv run ruff check src/ tests/
uv run cowork-graph build
uv run cowork-graph audit
```

Confirm in the audit output: the new check appears in `summary`, its count is plausible, and `total_findings` moved by that amount. Then check both renderers actually render it:

```bash
uv run cowork-graph audit --write   # writes markdown to the cowork audits dir
```

Read the generated report and confirm the new section shows formatted findings — not a raw dict. A check that runs but renders as `{'path': '...'}` passes every test and is still broken.

Finally, suppression round-trip: add a temporary entry for one real finding to the suppressions file, re-run `audit`, confirm the count drops by one and `suppressed` reports it — **then remove the temporary entry**. This is the only way to prove point 8 is wired, and it's the point most likely to be wrong.

## Ship it

Server-side code changed, so the running unit is now stale. Run `/ship-graph`.
