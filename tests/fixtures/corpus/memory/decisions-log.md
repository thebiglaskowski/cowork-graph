---
tags:
  - type/log
---

# Decisions Log

## Format

Each decision entry uses this structure:

```
### YYYY-MM-DD — Title

**Decision:** What was decided.
**Why:** The reasoning.
**Alternatives considered:** What else was on the table.
**Principle in play:** Optional principle (may be omitted).
**Source / context:** Where this came from.
```

---

### 2026-05-05 — Choose SQLite as backend

**Decision:** Use SQLite via Python stdlib sqlite3.
**Why:** Zero extra dependency, single file, easy to inspect with DB Browser.
**Alternatives considered:** Kuzu (nicer Cypher but extra dep), DuckDB (OLAP focus, overkill).
**Principle in play:** Keep the dependency surface tight.
**Source / context:** Kickoff conversation on [plan](../autoscriptstudio/autoscript-hub.md).

### 2026-05-05 — Incomplete entry missing required fields

**Decision:** This entry is missing Why and Source / context.
**Alternatives considered:** N/A.
