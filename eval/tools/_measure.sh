#!/usr/bin/env bash
# Measure first-try MCP recall reliability for the plugin memory path.
# Usage: bash _measure.sh <strategy> <trials>
#   strategy: baseline | debugfile | mcplist
#
# Measured first-try recall (20 trials, claude-haiku-4-5, headless -p):
#   baseline (plain claude -p)        8/20  = 40%   <- the startup race
#   debugfile (latency only)         13/20  = 65%   (not deterministic)
#   MCP_TIMEOUT / mcp-list gate              no effect / dead end
# The shipped fix (a priming turn over stream-json, see cli.run_claude_primed) is
# validated end-to-end through the real agent path by tools/_after_e2e.py:
#   AFTER (priming, max_tries=1)     20/20  = 100%
set -u
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_AUTH_TOKEN
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
export PYTHONUNBUFFERED=1
STRAT="${1:-baseline}"
N="${2:-20}"
WT=/mnt/c/Users/kenhu/amh-mcp-fix
PY=/home/kenhu/.venvs/swebench/bin/python
C=/home/kenhu/.local/bin/claude
export PYTHONPATH="$WT/eval"

# verify memeval comes from our worktree
SRC=$("$PY" -c 'import memeval,inspect;print(inspect.getfile(memeval))')
case "$SRC" in
  "$WT"/*) : ;;
  *) echo "FATAL: memeval not from worktree: $SRC"; exit 2 ;;
esac

NEEDLE="ZEPHYR-$(date +%s)-$RANDOM"
D=$(mktemp -d); mkdir -p "$D/memory"
"$PY" - "$D/memory" "$NEEDLE" <<PY
import sys
from memeval.okf import OKFStore
from memeval.schema import MemoryItem
OKFStore(sys.argv[1]).write(MemoryItem(item_id="m1", content="The secret project code is "+sys.argv[2]+".", timestamp=0.0))
PY

PORT=$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
"$PY" -m memeval.claudecode.memory_server --bundle "$D/memory" --log "$D/recall.jsonl" \
   --transport http --host 127.0.0.1 --port "$PORT" >"$D/srv.log" 2>&1 &
SRV=$!
for i in $(seq 1 50); do "$PY" -c "import socket,sys;s=socket.socket();sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" && break; sleep 0.2; done
cat > "$D/.mcp.json" <<EOF
{"mcpServers": {"memeval-memory": {"type": "http", "url": "http://127.0.0.1:$PORT/mcp"}}}
EOF

SYS="You have persistent memory via the memory_recall and memory_remember tools. ALWAYS call memory_recall with the question before answering, use the returned notes, and answer concisely with just the final answer."
PRE="First call the memory_recall tool with the question to retrieve relevant prior context, then answer concisely with just the final answer."
Q="$PRE

What is the secret project code?"
cd "$D"

ok=0; both=0
for t in $(seq 1 "$N"); do
  before=$([ -f "$D/recall.jsonl" ] && wc -l < "$D/recall.jsonl" || echo 0)

  case "$STRAT" in
    mcplist)
      # readiness gate: poll claude mcp list until our server is connected
      for g in $(seq 1 25); do
        if "$C" mcp list --mcp-config "$D/.mcp.json" --strict-mcp-config 2>/dev/null | grep -qiE 'memeval-memory.*(Connected|✓|connected)'; then break; fi
        sleep 0.2
      done
      ;;
  esac

  EXTRA=()
  case "$STRAT" in
    debugfile) EXTRA=(--debug-file "$D/dbg_$t.log") ;;
  esac

  "$C" -p "$Q" \
    --output-format json --permission-mode bypassPermissions --model claude-haiku-4-5 \
    --mcp-config "$D/.mcp.json" --strict-mcp-config \
    --allowedTools "mcp__memeval-memory__memory_recall,mcp__memeval-memory__memory_remember" \
    --append-system-prompt "$SYS" "${EXTRA[@]}" \
    >"$D/out_$t.json" 2>"$D/err_$t.log"

  after=$([ -f "$D/recall.jsonl" ] && wc -l < "$D/recall.jsonl" || echo 0)
  ans=$("$PY" -c 'import json,sys;print((json.load(open(sys.argv[1])).get("result") or "").replace(chr(10)," "))' "$D/out_$t.json" 2>/dev/null)
  called="NOT"; hasneedle="no"
  if [ "$after" -gt "$before" ]; then called="CALLED"; ok=$((ok+1)); fi
  case "$ans" in *"$NEEDLE"*) hasneedle="yes" ;; esac
  if [ "$called" = "CALLED" ] && [ "$hasneedle" = "yes" ]; then both=$((both+1)); fi
  printf "  %2d: %-7s needle=%-3s | %s\n" "$t" "$called" "$hasneedle" "${ans:0:70}"
done
echo "STRATEGY=$STRAT  recall_first_try=$ok/$N  recall+needle=$both/$N"
kill "$SRV" 2>/dev/null
rm -rf "$D"
