#!/usr/bin/env bash
# agy full50 V5 — 3 arms (base/builtin/plugin) x 50 sympy tasks, with the --add-dir fix.
# Durable (setsid nohup). Poll runs/sympy50v5-agy/status.txt.
source ~/.profile 2>/dev/null || true
cd /mnt/c/Users/kenhu/agent-memory-harness/eval || exit 3
OUT=/mnt/c/Users/kenhu/agent-memory-harness/runs/sympy50v5-agy
ST="$OUT/status.txt"
echo "DRIVER_START=$(date -u +%FT%TZ) variant=$DREAM_EXTRACTION_VARIANT keylen=${#OPENROUTER_API_KEY}" > "$ST"
for arm in base builtin plugin; do
  echo "ARM_START stage=$arm $(date -u +%FT%TZ)" >> "$ST"
  uv run --no-project python ../runs/sympy3-agy/agy_runner.py --arm "$arm" --limit 50 --timeout 600 --out "$OUT" >> "$OUT/$arm.log" 2>&1
  echo "ARM_EXIT stage=$arm rc=$? $(date -u +%FT%TZ)" >> "$ST"
done
echo "DRIVER_DONE=$(date -u +%FT%TZ)" >> "$ST"
