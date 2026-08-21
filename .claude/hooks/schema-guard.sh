#!/usr/bin/env bash
# PreToolUse hook: guard the frozen graph schema.
#
# CLAUDE.md states the rule flatly:
#   "Do not make schema changes without first reading plan.md from the cowork side."
#
# plan.md lives on the cowork half of this pair and is the source of truth for
# the Phase 1 schema. This hook turns that written rule into an enforced gate on
# src/cowork_graph/schema.sql — the actual frozen artifact.
#
# Scope is deliberately narrow: schema.sql only, NOT db.py. db.py is 355 lines of
# which only the bootstrap touches schema; a guard that fires on routine upsert
# work gets disabled within a week, and then it protects nothing.
#
# Exit 2 = block (message on stdout is fed back to Claude). Exit 0 = allow.

INPUT=$(cat)

FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import json,sys
try:
    print(json.load(sys.stdin).get('tool_input',{}).get('file_path','') or '')
except Exception:
    print('')
" 2>/dev/null)

[ -n "$FILE_PATH" ] || exit 0

case "$(basename "$FILE_PATH")" in
  schema.sql) ;;
  *) exit 0 ;;
esac

# Only guard THIS project's schema, not any stray schema.sql elsewhere.
case "$FILE_PATH" in
  *cowork_graph/schema.sql) ;;
  *) exit 0 ;;
esac

PLAN_WSL="/mnt/c/Users/joela/cowork/claude-environment/cowork-graph/plan.md"

cat <<MSG
BLOCKED: src/cowork_graph/schema.sql is the frozen Phase 1 graph schema.

CLAUDE.md: "Do not make schema changes without first reading plan.md from the
cowork side." The cowork side wins all design disputes — this repo implements
that plan, it is not the source of truth for it.

Read the canonical plan first:
  $PLAN_WSL

Then, if the change is still right:
  1. Confirm plan.md actually sanctions it (or update plan.md first, cowork-side).
  2. Remember schema.sql is bootstrap-only — existing .db files are NOT migrated
     by editing it. Derived state is rebuilt, so plan a 'cowork-graph reindex'.
  3. Re-run this edit; to proceed anyway, ask Joe to bypass the hook.
MSG
exit 2
