#!/bin/bash
# run-sandboxed.sh — launch board/serve.py with the narrowest filesystem view
# the host can give it.
#
# Three tiers, in order of preference:
#   1. Linux + bwrap      : --unshare-all --share-net, every control file bound
#                           read-only, nothing else visible.
#   2. macOS + sandbox-exec: read-only seatbelt profile.
#   3. neither            : prints "unsandboxed dev mode" and runs directly.
#
# The bind list is the mechanised form of the leader/worker protocol boundary:
# artifacts/ is NEVER bound. A board process that cannot open a worker's
# artifact cannot leak it, whatever the code does.
#
# Usage: run-sandboxed.sh --root <dir> [--adapter native|teams|auto] [--port N] [--host ADDR]
#        run-sandboxed.sh --teams-root <dir>   # teams compatibility mode
#        run-sandboxed.sh --root <dir> --print-plan     # dry run
#        run-sandboxed.sh --root <dir> --print-profile  # seatbelt profile

set -uo pipefail

BOARD_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SERVE="$BOARD_DIR/serve.py"
REPO_ROOT=$(cd "$BOARD_DIR/.." && pwd -P)
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

DATA_ROOT=""
ADAPTER="auto"
PORT="8737"
HOST="0.0.0.0"
FORCE_MODE=""
PRINT_PLAN=0
PRINT_PROFILE=0
TEAMS_COMPAT=0

die() {
  printf 'run-sandboxed: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'Usage: run-sandboxed.sh --root <dir> [--adapter native|teams] [--port N] [--host ADDR]\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root | --board-root)
      [ "$#" -ge 2 ] || die "missing value for $1"
      DATA_ROOT="$2"
      shift 2
      ;;
    --teams-root)
      [ "$#" -ge 2 ] || die "missing value for --teams-root"
      DATA_ROOT="$2"
      ADAPTER="teams"
      TEAMS_COMPAT=1
      shift 2
      ;;
    --adapter)
      [ "$#" -ge 2 ] || die "missing value for --adapter"
      ADAPTER="$2"
      shift 2
      ;;
    --port)
      [ "$#" -ge 2 ] || die "missing value for --port"
      PORT="$2"
      shift 2
      ;;
    --host)
      [ "$#" -ge 2 ] || die "missing value for --host"
      HOST="$2"
      shift 2
      ;;
    --mode) # bwrap | sandbox-exec | dev
      [ "$#" -ge 2 ] || die "missing value for --mode"
      FORCE_MODE="$2"
      shift 2
      ;;
    --print-plan)
      PRINT_PLAN=1
      shift
      ;;
    --print-profile)
      PRINT_PROFILE=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -n "$DATA_ROOT" ] || die "missing --root (or --teams-root)"
[ -d "$DATA_ROOT" ] || die "data root is not a directory: $DATA_ROOT"
[ -f "$SERVE" ] || die "missing serve.py: $SERVE"

DATA_ROOT=$(cd "$DATA_ROOT" && pwd -P) || die "cannot resolve data root"

if [ "$TEAMS_COMPAT" -eq 1 ]; then
  case "${DATA_ROOT##*/}" in
    .teams) ;;
    *)
      if [ -d "$DATA_ROOT/.teams" ]; then
        DATA_ROOT="$DATA_ROOT/.teams"
      fi
      ;;
  esac
fi

PYTHON=$(command -v python3) || die "python3 not found"
PYTHON_REAL=$(readlink -f "$PYTHON" 2>/dev/null) || PYTHON_REAL="$PYTHON"
[ -n "$PYTHON_REAL" ] || PYTHON_REAL="$PYTHON"

# ---------------------------------------------------------------------------
# Whitelist definition
# ---------------------------------------------------------------------------

NATIVE_FILES=(
  board.yaml
  board.json
  events.jsonl
)
NATIVE_DIRS=(
  tasks
  receipts
  notes
)

TEAM_FILES=(
  board.tsv
  worktrees.tsv
  agents.tsv
  workers.tsv
  flow.tsv
  mode.md
  team-meta.env
  team.meta
)
TEAM_DIRS=(
  receipts
  tasks
)

# ---------------------------------------------------------------------------
# Bind plan
# ---------------------------------------------------------------------------

BIND_SRC=()
BIND_DST=()

add_bind() {
  [ -e "$1" ] || return 0
  BIND_SRC+=("$1")
  BIND_DST+=("$2")
}

# The whole board/ directory
add_bind "$BOARD_DIR" "$BOARD_DIR"

# Top-level repo root if board is a submodule/subdirectory (for imports)
REPO_ROOT=$(cd "$BOARD_DIR/.." && pwd -P)
if [ -d "$REPO_ROOT/board" ]; then
  add_bind "$REPO_ROOT" "$REPO_ROOT"
fi

# Detect data mode
IS_TEAMS=0
if [ "$ADAPTER" = "teams" ] || [ "$ADAPTER" = "tmux-agent-teams" ] || [ "${DATA_ROOT##*/}" = ".teams" ]; then
  IS_TEAMS=1
fi

if [ "$IS_TEAMS" -eq 1 ]; then
  # Bind teams directory contents
  for team_path in "$DATA_ROOT"/*; do
    [ -d "$team_path" ] || continue
    team_name="${team_path##*/}"
    case "$team_name" in
      .* | *[!A-Za-z0-9._-]*) continue ;;
    esac
    for entry in "${TEAM_FILES[@]}"; do
      add_bind "$team_path/$entry" "$team_path/$entry"
    done
    for entry in "${TEAM_DIRS[@]}"; do
      add_bind "$team_path/$entry" "$team_path/$entry"
    done
  done
else
  # Native mode binds
  # 1. Check top-level DATA_ROOT files
  for entry in "${NATIVE_FILES[@]}"; do
    add_bind "$DATA_ROOT/$entry" "$DATA_ROOT/$entry"
  done
  for entry in "${NATIVE_DIRS[@]}"; do
    add_bind "$DATA_ROOT/$entry" "$DATA_ROOT/$entry"
  done

  # 2. Check if DATA_ROOT/.agent-board exists
  if [ -d "$DATA_ROOT/.agent-board" ]; then
    for entry in "${NATIVE_FILES[@]}"; do
      add_bind "$DATA_ROOT/.agent-board/$entry" "$DATA_ROOT/.agent-board/$entry"
    done
    for entry in "${NATIVE_DIRS[@]}"; do
      add_bind "$DATA_ROOT/.agent-board/$entry" "$DATA_ROOT/.agent-board/$entry"
    done
  fi

  # 3. Check subdirectories under DATA_ROOT
  for sub_path in "$DATA_ROOT"/*; do
    [ -d "$sub_path" ] || continue
    sub_name="${sub_path##*/}"
    case "$sub_name" in
      .* | *[!A-Za-z0-9._-]*) continue ;;
    esac
    for entry in "${NATIVE_FILES[@]}"; do
      add_bind "$sub_path/$entry" "$sub_path/$entry"
      add_bind "$sub_path/.agent-board/$entry" "$sub_path/.agent-board/$entry"
    done
    for entry in "${NATIVE_DIRS[@]}"; do
      add_bind "$sub_path/$entry" "$sub_path/$entry"
      add_bind "$sub_path/.agent-board/$entry" "$sub_path/.agent-board/$entry"
    done
  done
fi

if [ "$PRINT_PLAN" -eq 1 ]; then
  if [ "$TEAMS_COMPAT" -eq 1 ]; then
    printf 'teams-root\t%s\n' "$DATA_ROOT"
  else
    printf 'root\t%s\n' "$DATA_ROOT"
  fi
  printf 'python\t%s\n' "$PYTHON_REAL"
  for i in "${!BIND_SRC[@]}"; do
    printf 'ro-bind\t%s\n' "${BIND_SRC[$i]}"
  done
  exit 0
fi

# ---------------------------------------------------------------------------
# Tier 1: bubblewrap
# ---------------------------------------------------------------------------

bwrap_works() {
  command -v bwrap >/dev/null 2>&1 || return 1
  bwrap --unshare-all --share-net --ro-bind /usr /usr /usr/bin/true \
    >/dev/null 2>&1
}

run_bwrap() {
  local args=(
    --unshare-all
    --share-net
    --die-with-parent
    --new-session
    --proc /proc
    --dev /dev
    --tmpfs /tmp
    --ro-bind "$PYTHON_REAL" "$PYTHON_REAL"
  )
  local path
  for path in /usr /lib /lib64 /bin /sbin /etc/ssl "${PYTHON_REAL%/bin/*}"; do
    [ -e "$path" ] || continue
    case " ${args[*]} " in
      *" $path $path "*) continue ;;
    esac
    args+=(--ro-bind "$path" "$path")
  done
  local i
  for i in "${!BIND_SRC[@]}"; do
    args+=(--ro-bind "${BIND_SRC[$i]}" "${BIND_DST[$i]}")
  done
  args+=(--chdir /)
  printf 'run-sandboxed: bubblewrap (artifacts/ not bound)\n' >&2
  exec bwrap "${args[@]}" \
    "$PYTHON_REAL" "$SERVE" --root "$DATA_ROOT" --adapter "$ADAPTER" \
    --host "$HOST" --port "$PORT"
}

# ---------------------------------------------------------------------------
# Tier 2: macOS sandbox-exec
# ---------------------------------------------------------------------------

emit_sandbox_profile() {
  local python_prefix
  python_prefix="${PYTHON_REAL%/*}"
  python_prefix="${python_prefix%/*}"

  printf '(version 1)\n'
  printf '(deny default)\n'
  printf '(allow process*)\n'
  printf '(allow sysctl*)\n'
  printf '(allow mach*)\n'
  printf '(allow signal)\n'
  printf '(allow ipc-posix*)\n'
  printf '(allow network-bind network-inbound)\n'
  printf '(allow file-read-metadata)\n'
  printf '(allow file-read* (literal "/"))\n'
  printf '(allow file-read* (subpath "/usr") (subpath "/System") (subpath "/Library"))\n'
  printf '(allow file-read* (subpath "/private/var/db") (subpath "/dev"))\n'
  printf '(allow file-read* (subpath %s))\n' "$(sb_quote "$python_prefix")"

  local i
  for i in "${!BIND_SRC[@]}"; do
    if [ -d "${BIND_SRC[$i]}" ]; then
      printf '(allow file-read* (subpath %s))\n' "$(sb_quote "${BIND_SRC[$i]}")"
    else
      printf '(allow file-read* (literal %s))\n' "$(sb_quote "${BIND_SRC[$i]}")"
    fi
  done

  printf '(allow file-read* (literal %s))\n' "$(sb_quote "$DATA_ROOT")"
  local sub_path sub_name
  for sub_path in "$DATA_ROOT"/*; do
    [ -d "$sub_path" ] || continue
    sub_name="${sub_path##*/}"
    case "$sub_name" in
      .* | *[!A-Za-z0-9._-]*) continue ;;
    esac
    printf '(allow file-read* (literal %s))\n' "$(sb_quote "$sub_path")"
  done
}

run_sandbox_exec() {
  local profile
  profile=$(mktemp "${TMPDIR:-/tmp}/board-sandbox-profile.XXXXXX") ||
    die "cannot create sandbox profile"
  trap 'rm -f "$profile"' EXIT
  emit_sandbox_profile > "$profile"

  printf 'run-sandboxed: sandbox-exec read-only profile (artifacts/ denied)\n' >&2
  exec sandbox-exec -f "$profile" \
    "$PYTHON_REAL" "$SERVE" --root "$DATA_ROOT" --adapter "$ADAPTER" \
    --host "$HOST" --port "$PORT"
}

sb_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

# ---------------------------------------------------------------------------
# Tier 3: unsandboxed
# ---------------------------------------------------------------------------

run_dev() {
  printf 'run-sandboxed: unsandboxed dev mode — no bwrap and no sandbox-exec on this host; the board process can read every file this user can, including artifacts/\n' >&2
  exec "$PYTHON_REAL" "$SERVE" --root "$DATA_ROOT" --adapter "$ADAPTER" \
    --host "$HOST" --port "$PORT"
}

if [ "$PRINT_PROFILE" -eq 1 ]; then
  emit_sandbox_profile
  exit 0
fi

case "$FORCE_MODE" in
  bwrap)
    command -v bwrap >/dev/null 2>&1 || die "bwrap not available"
    run_bwrap
    ;;
  sandbox-exec)
    command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available"
    run_sandbox_exec
    ;;
  dev) run_dev ;;
  "") ;;
  *) die "unknown --mode: $FORCE_MODE" ;;
esac

if [ "$(uname -s)" = "Linux" ] && bwrap_works; then
  run_bwrap
elif command -v sandbox-exec >/dev/null 2>&1; then
  run_sandbox_exec
else
  run_dev
fi
