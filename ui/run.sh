#!/usr/bin/env bash
# Cookbook memory UI launcher.
#
# Hosts BOTH views under one origin: the live operator monitor (in-flight
# benchmark runs) and the memory-store inspector (browse + routing-effectiveness
# + query probe). Run from anywhere — the script `cd`s to the repo root so the
# newest results/v*/_memory auto-discovery works and `memeval` is importable
# from the repo venv after `make setup`.
#
#   ./ui/run.sh                       # both views; monitor opens to newest active run
#   ./ui/run.sh --open                # open browser on start
#   ./ui/run.sh --seed                # synthetic inspector corpus (no real run needed)
#   ./ui/run.sh --store /path/_memory # inspector substrate override
#   ./ui/run.sh --port 9001           # bind a different port
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"     # repo root (parent of ui/)
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"   # fall back to python3 if the repo venv is absent
cd "$ROOT"
exec env PYTHONPATH="$ROOT" "$PY" -m ui "$@"
