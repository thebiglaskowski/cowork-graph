#!/bin/sh
# Install the cowork-graph git hooks into the cowork git repo.
#
# Usage: sh scripts/install-hook.sh [/path/to/cowork]
#
# Defaults to /mnt/c/Users/joela/cowork if no argument is given.
# Safe to re-run — idempotent per hook; will not double-insert a hook line.
# Appends to an existing hook (e.g. git-lfs) rather than overwriting it.
#
# Three hooks get installed:
#
#   post-commit   `update --since HEAD~1` — the commit you just made.
#   post-merge    `update --since ORIG_HEAD` — everything a pull brought in.
#   post-rewrite  `update --since ORIG_HEAD` — everything a rebasing pull replayed.
#                 Guarded to $1 = "rebase" so `git commit --amend` doesn't
#                 double-fire against a post-commit run that already covered it.
#
# post-merge exists because the graph DB is derived per-machine and never
# committed. Without it, the machine doing the pulling never sees the other
# machine's edits until its own next commit — and that commit only sweeps
# HEAD~1, so a multi-commit pull stays permanently missing from the graph.
# post-merge fires on fast-forward pulls too (verified: arg=0, ORIG_HEAD set),
# which is the common two-machine sync case. On a true merge pull the CLI's
# own is_merge_commit(HEAD) check upgrades it to a full rebuild.
#
# Cross-surface: the cowork repo is committed from both WSL git and Windows
# git. A WSL commit runs the venv CLI directly. A Windows commit runs its
# hook under Git-for-Windows' MSYS sh, where /home/joe/... does not resolve
# to the WSL filesystem — so that case routes the call through wsl.exe.
#
# NOTE: .git/hooks is not tracked by git, so this must be run once per
# machine. The machine that pulls most is the one that needs post-merge.

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COWORK_DIR="${1:-/mnt/c/Users/joela/cowork}"

if [ ! -d "$COWORK_DIR/.git" ]; then
    echo "Error: not a git repository: $COWORK_DIR" >&2
    exit 1
fi

# The hook always invokes the project-venv CLI by its absolute WSL path. That
# path works from a WSL commit (run directly) and from a Windows commit (run
# via wsl.exe), so confirm it exists before installing.
if [ ! -x "$REPO_DIR/.venv/bin/cowork-graph" ]; then
    echo "Error: $REPO_DIR/.venv/bin/cowork-graph not found. Run 'uv sync' in $REPO_DIR first." >&2
    exit 1
fi

# install_hook <hook-name> <git-ref> [guard-line]
#
# Appends the cowork-graph sync block to the named hook, resolving the graph
# update against <git-ref>. Optional guard-line is emitted before the block
# and can early-exit the hook (used to limit post-rewrite to rebases).
# Idempotent per hook file — each is checked on its own path, so adding a
# hook later doesn't get gated by an earlier install.
install_hook() {
    hook_name="$1"
    since_ref="$2"
    guard_line="$3"
    hook_path="$COWORK_DIR/.git/hooks/$hook_name"

    # Match the marker comment, which is literally present in the emitted block.
    # (An earlier version grepped for "cowork-graph update" — that string is
    # never emitted, since the block invokes "$CG_BIN" update, so the guard
    # silently never fired and re-running double-appended the block.)
    if grep -q "^# cowork-graph sync" "$hook_path" 2>/dev/null; then
        echo "Hook already installed: $hook_path"
        return 0
    fi

    # Create hook file with shebang if it doesn't exist yet
    if [ ! -f "$hook_path" ]; then
        printf '#!/bin/sh\n' > "$hook_path"
        chmod +x "$hook_path"
    fi

    # Append (preserves any existing hook content, e.g. git-lfs)
    cat >> "$hook_path" <<HOOK
# cowork-graph sync — installed by $REPO_DIR/scripts/install-hook.sh
# Cross-surface: a WSL run invokes the CLI directly; a Windows run routes via wsl.exe.
${guard_line}
CG_BIN="$REPO_DIR/.venv/bin/cowork-graph"
if [ -x "\$CG_BIN" ]; then
  "\$CG_BIN" update --since $since_ref </dev/null &
elif command -v wsl.exe >/dev/null 2>&1; then
  MSYS_NO_PATHCONV=1 wsl.exe bash -lc "\$CG_BIN update --since $since_ref" </dev/null &
fi
HOOK

    echo "Hook installed: $hook_path"
}

install_hook post-commit HEAD~1

# A pull that fast-forwards fires post-merge — even under pull.rebase=true.
install_hook post-merge ORIG_HEAD

# A pull that actually rebases (local commits present) does NOT fire
# post-merge; it fires post-rewrite with $1="rebase". ORIG_HEAD is the
# pre-rebase local tip, so ORIG_HEAD..HEAD is exactly the pulled-in work.
# Guarded to rebase only: post-rewrite also fires on `git commit --amend`,
# which post-commit already covers and where ORIG_HEAD is unrelated.
install_hook post-rewrite ORIG_HEAD '[ "$1" = "rebase" ] || exit 0'
