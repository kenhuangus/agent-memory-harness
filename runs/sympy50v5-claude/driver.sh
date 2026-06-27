#!/usr/bin/env bash
# FULL V5 final run: claude-sonnet-4-6 over the complete sympy_sympy_sequence
# (50 tasks), 3 arms sequentially (base, builtin, plugin-blank). Detached+durable.
# V5 daydream extraction. Sources ~/.profile for OPENROUTER_API_KEY + V5 default.
set -o pipefail
source ~/.profile 2>/dev/null || true
export DREAM_EXTRACTION_VARIANT=V5
export PATH="$HOME/.local/bin:$PATH"
R=/mnt/c/Users/kenhu/agent-memory-harness
cd "$R" || exit 3
D=runs/sympy50v5-claude
mkdir -p "$D"
ST="$D/status.txt"
echo "DRIVER_START=$(date -u +%FT%TZ) variant=$DREAM_EXTRACTION_VARIANT openrouter_keylen=${#OPENROUTER_API_KEY}" > "$ST"

run_arm () {
  local stage="$1" rv="$2"
  echo "ARM_START stage=$stage $(date -u +%FT%TZ)" >> "$ST"
  make pipeline ARGS="--yes --benchmark swe_bench_cl --sequence sympy_sympy_sequence --stage $stage --limit 50 --model claude-sonnet-4-6 --grader swebench --results-version $rv" > "$D/$stage.log" 2>&1
  echo "ARM_EXIT stage=$stage rc=$? $(date -u +%FT%TZ)" >> "$ST"
}

run_arm base         sympy50v5-claude-base
run_arm builtin      sympy50v5-claude-builtin
run_arm plugin-blank sympy50v5-claude-plugin
echo "DRIVER_DONE=$(date -u +%FT%TZ)" >> "$ST"
