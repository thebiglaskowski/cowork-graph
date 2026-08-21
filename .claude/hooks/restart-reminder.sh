#!/usr/bin/env bash
# Stop hook: warn when the running cowork-graph MCP server is older than its code.
#
# The failure this catches: edits to src/cowork_graph/ pass all 377 tests and
# commit clean, but the cowork-graph-mcp systemd unit keeps serving the OLD code
# until it is restarted. Tests green + clean commit reads as success while the
# live server is stale — exactly the "expensive failure that looks like success"
# the fail-loud rule exists to catch.
#
# Stop (not PostToolUse) on purpose: one check at the end of the turn, rather
# than a restart nag on every mid-session edit.
#
# ADVISORY ONLY — emits additionalContext and exits 0. It never blocks stopping.
# Silent no-op when: not this repo, systemctl absent, or the unit isn't active.

UNIT="cowork-graph-mcp.service"
SRC_SUBDIR="src/cowork_graph"

cat >/dev/null 2>&1  # drain the Stop payload; nothing in it is needed

command -v systemctl >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$REPO_ROOT/$SRC_SUBDIR" ] || exit 0

# Only speak up about a unit that is actually running. A missing unit reports
# "inactive" with exit 0, so this covers the not-installed case too.
[ "$(systemctl --user show -p ActiveState --value "$UNIT" 2>/dev/null)" = "active" ] || exit 0

STARTED_RAW=$(systemctl --user show -p ActiveEnterTimestamp --value "$UNIT" 2>/dev/null)
[ -n "$STARTED_RAW" ] || exit 0
STARTED=$(date -d "$STARTED_RAW" +%s 2>/dev/null) || exit 0
[ -n "$STARTED" ] || exit 0

# Newest mtime among the modules the server imports.
NEWEST=0
NEWEST_FILE=""
while IFS= read -r f; do
  m=$(stat -c %Y "$f" 2>/dev/null) || continue
  if [ "$m" -gt "$NEWEST" ]; then NEWEST=$m; NEWEST_FILE=$f; fi
done < <(find "$REPO_ROOT/$SRC_SUBDIR" -name '*.py' -not -path '*/__pycache__/*' 2>/dev/null)

[ "$NEWEST" -gt "$STARTED" ] 2>/dev/null || exit 0

REL="${NEWEST_FILE#"$REPO_ROOT"/}"
AGE_MIN=$(( (NEWEST - STARTED) / 60 ))

python3 - "$UNIT" "$REL" "$STARTED_RAW" "$AGE_MIN" <<'PY'
import json, sys
unit, rel, started, age = sys.argv[1:5]
msg = (
    f"STALE SERVER: {unit} has been running since {started}, but {rel} was "
    f"modified {age} minute(s) after that. The live MCP server on :8765 is "
    "serving OLD code — tests passing and a clean commit do NOT mean the "
    "change shipped.\n"
    f"  Restart:    systemctl --user restart {unit}\n"
    f"  Verify:     systemctl --user show -p NRestarts --value {unit}   # expect no crash loop\n"
    "  Smoke test: POST tools/list to http://127.0.0.1:8765/mcp\n"
    "Or run /ship-graph, which does all three and reports each step."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "Stop",
        "additionalContext": msg,
        "server_stale": True,
        "newest_file": rel,
    }
}))
PY
exit 0
