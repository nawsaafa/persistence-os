#!/usr/bin/env bash
# Install persistence-os git hooks into .git/hooks/.
# One-time per clone. Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SOURCE_DIR="$REPO_ROOT/scripts/git-hooks"

if [[ ! -d "$REPO_ROOT/.git" ]]; then
    echo "error: $REPO_ROOT/.git not found — run from a clone, not a worktree" >&2
    exit 1
fi

mkdir -p "$HOOKS_DIR"

for hook in pre-push; do
    src="$SOURCE_DIR/$hook"
    dst="$HOOKS_DIR/$hook"

    if [[ ! -f "$src" ]]; then
        echo "error: $src not found" >&2
        exit 1
    fi

    if [[ -f "$dst" && ! -L "$dst" ]]; then
        echo "warning: $dst exists and is not a symlink — backing up to $dst.bak" >&2
        mv "$dst" "$dst.bak"
    fi

    chmod +x "$src"
    ln -sf "$src" "$dst"
    echo "installed: $dst -> $src"
done
