#!/bin/sh
# Install the cowork-graph post-commit hook into the cowork git repo.
#
# Usage: sh scripts/install-hook.sh [/path/to/cowork]
#
# Defaults to /mnt/c/Users/joela/cowork if no argument is given.
# The hook calls `cowork-graph update --since HEAD~1` after every commit,
# falling back to a full rebuild on merge commits (handled inside the CLI).

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COWORK_DIR="${1:-/mnt/c/Users/joela/cowork}"
HOOK_PATH="$COWORK_DIR/.git/hooks/post-commit"

if [ ! -d "$COWORK_DIR/.git" ]; then
    echo "Error: not a git repository: $COWORK_DIR" >&2
    exit 1
fi

if ! command -v cowork-graph >/dev/null 2>&1; then
    echo "Error: cowork-graph not found on PATH. Run 'uv sync' in $REPO_DIR first." >&2
    exit 1
fi

cat > "$HOOK_PATH" <<'HOOK'
#!/bin/sh
# cowork-graph sync hook — installed by scripts/install-hook.sh
cowork-graph update --since HEAD~1 &
HOOK

chmod +x "$HOOK_PATH"
echo "Hook installed: $HOOK_PATH"
