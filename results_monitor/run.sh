#!/usr/bin/env bash
# Convenience wrapper — mirrors router_ui/run.sh.
# Runs from the repo root with PYTHONPATH set so `python -m results_monitor` works.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
exec uv run --no-project python -m results_monitor "$@"
