#!/usr/bin/env bash
# FULL V5 final run: grok over the complete sympy_sympy_sequence (50 tasks),
# 3 arms sequentially (base, builtin, plugin). Detached+durable. V5 daydream
# extraction. Reuses runs/sympy50v5-grok/run_grok.py (RUNDIR=sympy50v5-grok)
# which drives grok --yolo (real file edits) via the unchanged grok_runner.
set -o pipefail
source ~/.profile 2>/dev/null || true
export DREAM_EXTRACTION_VARIANT=V5
R=/mnt/c/Users/kenhu/agent-memory-harness
cd "$R/eval" || exit 3
export GROK_LIMIT=50
export GROK_TIMEOUT=1800
D="$R/runs/sympy50v5-grok"
mkdir -p "$D"/{base,builtin,plugin}
ST="$D/status.txt"
echo "DRIVER_START=$(date -u +%FT%TZ) variant=$DREAM_EXTRACTION_VARIANT openrouter_keylen=${#OPENROUTER_API_KEY} limit=$GROK_LIMIT" > "$ST"
for arm in base builtin plugin; do
  echo "ARM_START stage=$arm $(date -u +%FT%TZ)" >> "$ST"
  uv run --no-project python ../runs/sympy50v5-grok/run_grok.py --arm "$arm" \
      > "$D/$arm/driver.out" 2>&1
  echo "ARM_EXIT stage=$arm rc=$? $(date -u +%FT%TZ)" >> "$ST"
done
echo "DRIVER_DONE=$(date -u +%FT%TZ)" >> "$ST"
