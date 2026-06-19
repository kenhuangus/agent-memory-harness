"""Run a benchmark (or all five) through the Claude Code CLI and log results.

    python -m memeval.claudecode.run_bench --benchmark longmemeval --mode plugin \
        --model claude-haiku-4-5 --limit 20 --results ../results.json

``--mode`` is off | builtin | plugin | all (all runs the three for comparison).
Reuses the standard harness (cost/budget, trajectory, metrics, results ledger),
so these runs show up on the Results page / scoreboard next to the API runs.
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

from ..cost import DEFAULT_BUDGET_USD
from ..schema import Benchmark
from .agent import ClaudeCodeAgent
from .platform import describe, detect

_ALL_BENCH = ["memoryagentbench", "longmemeval", "swe_contextbench", "swe_bench_cl", "contextbench"]
_MODES = ["off", "builtin", "plugin"]


def _resolve_path(benchmark: str, path: Optional[str]) -> Optional[str]:
    """``--path fixtures`` -> the bundled per-benchmark fixture; else passthrough."""
    if path == "fixtures":
        from pathlib import Path
        fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / f"{benchmark}.json"
        return str(fx)
    return path


def _run_one(benchmark: str, mode: str, args: argparse.Namespace) -> Optional[dict]:
    from ..agent import run_agent
    from ..cost import CostTracker
    from ..results import append_result

    agent = ClaudeCodeAgent(model=args.model, memory_mode=mode, k=args.k, timeout=args.timeout)
    cost = CostTracker(budget_usd=args.budget_usd) if args.budget_usd and args.budget_usd > 0 else None
    try:
        rr = run_agent(
            Benchmark.from_str(benchmark), agent, memory=(mode != "off"),
            limit=args.limit, dev_slice=args.dev_slice,
            path_or_id=_resolve_path(benchmark, args.path),
            cost=cost, k=args.k, seed_sessions=False,  # the agent seeds memory itself
        )
    except Exception as exc:  # surface per-(benchmark,mode) failure, keep going
        print(f"FAIL {benchmark:18} {mode:8} {type(exc).__name__}: {str(exc)[:140]}")
        return None
    rec = append_result(rr, args.results, run_id=f"claude-code-{mode}",
                        notes=f"Claude Code CLI · memory={mode}")
    m = rr.metrics
    print(f"OK   {benchmark:18} {mode:8} acc={m.accuracy:.2f} rel={m.relevancy:.2f} "
          f"rec={m.recency:.2f} eff={m.efficiency:.2f} n={rr.n_tasks} ${rr.cost_usd:.4f}")
    return rec


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="memeval.claudecode.run_bench")
    ap.add_argument("--benchmark", default="all", help="one of the five, or 'all'.")
    ap.add_argument("--mode", default="plugin", help="off | builtin | plugin | all.")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--path", default=None, help="local fixture/dataset path (blank = real source).")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dev-slice", type=float, default=None)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--results", default="results.json")
    args = ap.parse_args(argv)

    print(describe())   # which CLI was detected (native / WSL / not found)
    if detect() is None:
        print("WARNING: runs will fail until 'claude' is installed (native or in WSL). "
              "`npm install -g @anthropic-ai/claude-code`; overrides: $CLAUDE_CLI / $CLAUDE_WSL_DISTRO.")

    benches = _ALL_BENCH if args.benchmark == "all" else [args.benchmark]
    modes = _MODES if args.mode == "all" else [args.mode]
    n_ok = 0
    for b in benches:
        for mode in modes:
            if _run_one(b, mode, args) is not None:
                n_ok += 1
    print(f"\n{n_ok}/{len(benches) * len(modes)} run(s) logged -> {args.results}")
    print("Scoreboard:  python -m memeval.results summary --path " + args.results)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
