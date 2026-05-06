#!/bin/sh
# Install the cowork-graph post-commit hook into the cowork git repo.
#
# Usage: sh scripts/install-hook.sh [/path/to/cowork]
#
# Defaults to /mnt/c/Users/joela/cowork if no argument is given.
# Safe to re-run — idempotent; will not double-insert the hook line.
# Appends to an existing hook (e.g. git-lfs) rather than overwriting it.
# The hook calls `cowork-graph update --since HEAD~1` after every commit;
# merge commits trigger a full rebuild (handled inside the CLI).

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COWORK_DIR="${1:-/mnt/c/Users/joela/cowork}"
HOOK_PATH="$COWORK_DIR/.git/hooks/post-commit"

if [ ! -d "$COWORK_DIR/.git" ]; then
    echo "Error: not a git repository: $COWORK_DIR" >&2
    exit 1
fi

# Resolve CLI: prefer PATH, fall back to project venv
if command -v cowork-graph >/dev/null 2>&1; then
    CLI="cowork-graph"
elif [ -x "$REPO_DIR/.venv/bin/cowork-graph" ]; then
    CLI="$REPO_DIR/.venv/bin/cowork-graph"
else
    echo "Error: cowork-graph not found. Run 'uv sync' in $REPO_DIR first." >&2
    exit 1
fi

# Idempotency check
if grep -q "cowork-graph update" "$HOOK_PATH" 2>/dev/null; then
    echo "Hook already installed: $HOOK_PATH"
    exit 0
fi

# Create hook file with shebang if it doesn't exist yet
if [ ! -f "$HOOK_PATH" ]; then
    printf '#!/bin/sh\n' > "$HOOK_PATH"
    chmod +x "$HOOK_PATH"
fi

# Append (preserves any existing hook content, e.g. git-lfs)
cat >> "$HOOK_PATH" <<HOOK
# cowork-graph sync — installed by $REPO_DIR/scripts/install-hook.sh
$CLI update --since HEAD~1 &
HOOK

echo "Hook installed: $HOOK_PATH (CLI: $CLI)"
