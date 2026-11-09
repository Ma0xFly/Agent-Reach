#!/usr/bin/env bash
# Rebase this fork onto Panniantong/Agent-Reach's main branch.
#
# The script never pushes. It stops on the first conflict, runs the full test
# suite and three read-only smoke commands after a clean rebase, and leaves the
# branch for the maintainer to review and push.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
UPSTREAM_REMOTE=${UPSTREAM_REMOTE:-upstream}
UPSTREAM_URL=${UPSTREAM_URL:-https://github.com/Panniantong/Agent-Reach.git}

usage() {
    cat <<'EOF'
Usage: scripts/upstream-sync.sh [--dry-run]

Fetch Panniantong/Agent-Reach and rebase the current branch onto its main.
  --dry-run   print the remote/fetch/rebase/check plan without changing anything
  --help      show this message

Environment:
  UPSTREAM_REMOTE  remote name to use (default: upstream)
  UPSTREAM_URL     URL used when that remote does not exist
                   (default: https://github.com/Panniantong/Agent-Reach.git)

The rebase uses --rebase-merges so this fork's integration merge commits and
their conflict resolutions are preserved. Never pushes to upstream.
EOF
}

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            usage
            exit 0
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        *)
            echo "error: unknown argument: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

cd "$REPO_ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: $REPO_ROOT is not a git checkout" >&2
    exit 1
fi

BRANCH=$(git branch --show-current)
if [ -z "$BRANCH" ]; then
    echo "error: detached HEAD; switch to the branch to sync first" >&2
    exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "Branch: $BRANCH"
    echo "Would run: git fetch $UPSTREAM_REMOTE main"
    echo "Would run: git rebase --rebase-merges $UPSTREAM_REMOTE/main"
    echo "Would run: pytest -q"
    echo "Would run: agent-reach channels"
    echo "Would run: agent-reach youtube channel @YouTube --limit 2"
    echo "Would run: agent-reach bilibili search 车联网 --limit 2"
    exit 0
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "error: tracked changes present; commit or stash them before syncing" >&2
    exit 1
fi

if git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
    UPSTREAM_RESOLVED=$(git remote get-url "$UPSTREAM_REMOTE")
    case "$UPSTREAM_RESOLVED" in
        *Ma0xFly/Agent-Reach*)
            echo "error: $UPSTREAM_REMOTE points at this fork, not upstream" >&2
            exit 1
            ;;
    esac
    echo "Using $UPSTREAM_REMOTE -> $UPSTREAM_RESOLVED"
else
    echo "Adding $UPSTREAM_REMOTE -> $UPSTREAM_URL"
    git remote add "$UPSTREAM_REMOTE" "$UPSTREAM_URL"
fi

PRE_REBASE=$(git rev-parse HEAD)
echo "Syncing $BRANCH from $PRE_REBASE"

git fetch "$UPSTREAM_REMOTE" main
if ! git rebase --rebase-merges "$UPSTREAM_REMOTE/main"; then
    cat >&2 <<EOF
Rebase stopped with conflicts. Resolve them, then:
  git add <resolved files>
  git rebase --continue

To abandon this sync and return to $PRE_REBASE:
  git rebase --abort

Known conflict files and resolution notes are in docs/FORK.md.
EOF
    exit 1
fi

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_CMD=("$REPO_ROOT/.venv/bin/python")
else
    PYTHON_CMD=(python3)
fi
if [ -x "$REPO_ROOT/.venv/bin/agent-reach" ]; then
    CLI=("$REPO_ROOT/.venv/bin/agent-reach")
else
    CLI=(agent-reach)
fi

check_failed() {
    echo "error: $1 failed; the rebase is applied but not verified." >&2
    echo "Return before the sync with: git reset --hard $PRE_REBASE" >&2
    exit 1
}

echo "[1/4] pytest -q"
"${PYTHON_CMD[@]}" -m pytest -q || check_failed "pytest"

echo "[2/4] agent-reach channels"
"${CLI[@]}" channels >/dev/null || check_failed "channels"

echo "[3/4] agent-reach youtube channel @YouTube --limit 2"
"${CLI[@]}" youtube channel "@YouTube" --limit 2 >/dev/null || check_failed "youtube channel"

echo "[4/4] agent-reach bilibili search 车联网 --limit 2"
"${CLI[@]}" bilibili search "车联网" --limit 2 >/dev/null || check_failed "bilibili search"

echo "Sync complete and verified. Review the branch, then push to your own fork."
echo "Never push this history to $UPSTREAM_REMOTE."