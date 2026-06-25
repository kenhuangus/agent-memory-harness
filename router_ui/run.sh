#!/usr/bin/env bash
# Router memory-inspector UI launcher.
#
# Inspects the memories the cookbook-memory plugin saves during a benchmark run and lets you
# evaluate how the router routed them. Runs from the repo root (so newest results/v*/_memory
# auto-discovery works) using the repo venv (which provides `memeval` after `make setup`), with
# the repo root on PYTHONPATH (which provides `router_ui`).
#
#   ./router_ui/run.sh                      # newest pipeline substrate (results/v*/_memory)
#   ./router_ui/run.sh --seed --open        # synthetic demo corpus (no real run needed)
#   ./router_ui/run.sh --store /path/_memory
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"     # repo root (parent of router_ui/)
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"   # fall back to python3 if the repo venv is absent
cd "$ROOT"
exec env PYTHONPATH="$ROOT" "$PY" -m router_ui "$@"
