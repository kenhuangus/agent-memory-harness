#!/usr/bin/env bash
#
# run_vista.sh — faithful, parameterized runner for the VISTA benchmark.
#
# Reproduces the exact run_bench invocation, env, and dream-consolidation loop
# that produced the VISTA 97-test-split run (originally driven by the untracked
# _split_pr.sh / _split_builtin.sh scripts).
#
#   Usage:
#     run_vista.sh <mode> <split> <limit> [workers]
#
#       mode    : off | builtin | plugin-real
#       split   : train | dev | test | challenge | all
#       limit   : 0 = all tasks in the split, or a positive integer cap
#       workers : (plugin-real only) --plugin-workers value; default 4
#
#   Examples:
#     run_vista.sh plugin-real test 0 4     # the 97-split plugin-real arm
#     run_vista.sh builtin     test 0       # the 97-split builtin arm
#     run_vista.sh off         test 0       # no-memory baseline
#     run_vista.sh plugin-real dev  8 4     # quick smoke
#
# Designed to run under WSL/Linux (the originals ran from /home/kenhu/vista-venv
# with PYTHONPATH=<repo>/eval). REPO is derived from the script location via
# git, never hardcoded.
#
set -uo pipefail

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-2}"
}

# --- args -------------------------------------------------------------------
[ "$#" -ge 3 ] || { echo "ERROR: expected 3-4 args, got $#" >&2; usage 2; }

MODE="$1"
SPLIT="$2"
LIMIT="$3"
WORKERS="${4:-4}"

case "$MODE" in
  off|builtin|plugin-real) ;;
  -h|--help|help) usage 0 ;;
  *) echo "ERROR: bad mode '$MODE' (want off|builtin|plugin-real)" >&2; usage 2 ;;
esac
case "$SPLIT" in
  train|dev|test|challenge|all) ;;
  *) echo "ERROR: bad split '$SPLIT' (want train|dev|test|challenge|all)" >&2; usage 2 ;;
esac
case "$LIMIT" in
  ''|*[!0-9]*) echo "ERROR: limit must be a non-negative integer (0 = all)" >&2; usage 2 ;;
esac

# --- repo root (script-relative, not hardcoded) -----------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"
[ -n "$REPO" ] || { echo "ERROR: could not resolve repo root from $SCRIPT_DIR" >&2; exit 1; }

# --- python / venv (mirror the originals' PATH preference) ------------------
# The 97-split run used /home/kenhu/vista-venv. Honor it if present; otherwise
# rely on whatever python is already on PATH (e.g. an activated venv).
export PATH="$HOME/.local/bin:$HOME/vista-venv/bin:$PATH"
export PYTHONPATH="$REPO/eval"

# --- VISTA dataset/split env (verbatim semantics) --------------------------
export VISTA_DATASET=full
export VISTA_SPLIT="$SPLIT"

# --- output dir -------------------------------------------------------------
OUT="$REPO/runs/vista/${SPLIT}/${MODE}"
mkdir -p "$OUT"
LOG="$OUT/driver.log"

# --- run_bench command -------------------------------------------------------
# Common flags shared by all arms (matches the originals verbatim):
#   --benchmark vista  --model claude-haiku-4-5  --grader none
# plugin-real adds the daydream env, --plugin-workers, the sandbox config dir,
# and the per-store `dream --all` consolidation loop.
CMD=(python -m memeval.claudecode.run_bench
     --benchmark vista
     --mode "$MODE"
     --limit "$LIMIT"
     --model claude-haiku-4-5
     --grader none
     --out-dir "$OUT")

if [ "$MODE" = "plugin-real" ]; then
  # Daydream write path: source OPENROUTER_API_KEY from the WSL claude-managed
  # block in ~/.profile / ~/.bashrc (exactly as _split_pr.sh did).
  eval "$(grep -h '^export OPENROUTER_API_KEY=' "$HOME/.profile" "$HOME/.bashrc" 2>/dev/null | head -1)"
  export DREAM_PROVIDER=openrouter DREAM_MODEL=deepseek/deepseek-chat-v3.1

  # plugin-real needs a logged-in Claude sandbox config dir for auth. The
  # 97-split run used .../agent-memory-harness-vcs/eval/.claude-sandbox-vcs.
  # Read from env if set; otherwise error with guidance (do NOT hardcode a
  # sibling-worktree path as the only option).
  if [ -z "${MEMEVAL_SANDBOX_CONFIG_DIR:-}" ]; then
    echo "ERROR: plugin-real requires MEMEVAL_SANDBOX_CONFIG_DIR pointing at a" >&2
    echo "       logged-in Claude sandbox config dir, e.g." >&2
    echo "         export MEMEVAL_SANDBOX_CONFIG_DIR=/path/to/eval/.claude-sandbox" >&2
    echo "       (the 97-split run used .../agent-memory-harness-vcs/eval/.claude-sandbox-vcs)." >&2
    exit 1
  fi
  if [ ! -d "$MEMEVAL_SANDBOX_CONFIG_DIR" ]; then
    echo "ERROR: MEMEVAL_SANDBOX_CONFIG_DIR='$MEMEVAL_SANDBOX_CONFIG_DIR' is not a directory." >&2
    exit 1
  fi
  export MEMEVAL_SANDBOX_CONFIG_DIR

  CMD+=(--plugin-workers "$WORKERS")
fi

# --- driver log header (timestamps + keylen, as the originals did) ----------
KEYLEN="${OPENROUTER_API_KEY:+${#OPENROUTER_API_KEY}}"
KEYLEN="${KEYLEN:-0}"
{
  echo "START=$(date -u +%FT%TZ) mode=$MODE split=$SPLIT limit=$LIMIT workers=$WORKERS keylen=$KEYLEN"
  echo "REPO=$REPO"
  echo "OUT=$OUT"
  echo "CMD=${CMD[*]}"
} > "$LOG"

cd "$REPO/eval" || { echo "ERROR: missing $REPO/eval" >&2; exit 1; }

"${CMD[@]}" > "$OUT/run.out" 2> "$OUT/run.err"
RC=$?
echo "${MODE}_EXIT=$RC at $(date -u +%FT%TZ)" >> "$LOG"

# --- dream consolidation loop (plugin-real only) ----------------------------
if [ "$MODE" = "plugin-real" ]; then
  DLOG="$OUT/dream_full.log"
  : > "$DLOG"
  for d in "$OUT"/_memory/*/.cookbook-memory; do
    [ -d "$d" ] || continue
    echo "=== dream --all $d ===" >> "$DLOG"
    python -m memeval.dreaming.cli dream --all --store "$d" >> "$DLOG" 2>&1
  done
  echo "DREAM_DONE at $(date -u +%FT%TZ)" >> "$DLOG"
fi

echo "Done (rc=$RC). Logs + results under: $OUT"
exit "$RC"
