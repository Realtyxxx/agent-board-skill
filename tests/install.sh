#!/bin/bash
# tests/install.sh — bin/install.js assertions.
#
# Covers:
#   1. --copy produces a self-contained install: board/ is a real directory,
#      not a symlink back into this repo.
#   2. Default mode symlinks the skill directory.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
INSTALLER="$REPO_ROOT/bin/install.js"

TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/install-test.XXXXXX")
cleanup() {
  rm -rf "$TEST_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'ok - %s\n' "$1"
}

command -v node >/dev/null 2>&1 || fail "node not found"

# 1. --copy
node "$INSTALLER" --dir "$TEST_ROOT/copy" --copy >/dev/null
DEST="$TEST_ROOT/copy/agent-board"
[ -f "$DEST/SKILL.md" ] || fail "--copy did not install SKILL.md"
[ -L "$DEST/board" ] && fail "--copy left board/ as a symlink -> $(readlink "$DEST/board")"
[ -f "$DEST/board/serve.py" ] || fail "--copy did not copy board/serve.py"
[ -x "$DEST/board/run-sandboxed.sh" ] || fail "--copy did not keep run-sandboxed.sh executable"
pass "1. --copy install is self-contained"

# 2. default symlink
node "$INSTALLER" --dir "$TEST_ROOT/link" >/dev/null
[ -L "$TEST_ROOT/link/agent-board" ] || fail "default install is not a symlink"
[ -f "$TEST_ROOT/link/agent-board/board/serve.py" ] || fail "symlinked install cannot reach board/serve.py"
pass "2. default install symlinks the skill"

# 3. --copy into the source tree (directly, or through the skill/board symlink)
#    must be refused up front instead of recursing into itself. Run against a
#    scratch copy so a regression cannot flood this checkout.
SCRATCH="$TEST_ROOT/repo"
mkdir -p "$SCRATCH"
cp -a "$REPO_ROOT/bin" "$REPO_ROOT/skill" "$REPO_ROOT/board" "$SCRATCH/"
for inside in skill/local board/local; do
  if timeout 20 node "$SCRATCH/bin/install.js" --copy --dir "$SCRATCH/$inside" >/dev/null 2>&1; then
    fail "--copy --dir $inside was not refused"
  fi
  [ -e "$SCRATCH/$inside" ] && fail "--copy --dir $inside created files before refusing"
done
pass "3. --copy into the source tree is refused"

pass "install.sh completed all tests successfully"
