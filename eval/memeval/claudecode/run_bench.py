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
# The comparison that matters: Claude Code's built-in memory vs our plugin memory.
# (`off` is still accepted as an explicit --mode, but it's not part of `all`.)
_MODES = ["builtin", "plugin"]


def _resolve_path(benchmark: str, path: Optional[str]) -> Optional[str]:
    """``--path fixtures`` -> the bundled per-benchmark fixture; else passthrough."""
    if path == "fixtures":
        from pathlib import Path
        fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / f"{benchmark}.json"
        return str(fx)
    return path


def _run_one(benchmark: str, mode: str, args: argparse.Namespace) -> Optional[dict]:
    import json
    import os
    from ..agent import run_agent
    from ..cost import CostTracker
    from ..results import append_result, result_record
    from ..trajectory import TrajectoryLogger

    # With --out-dir, each run is self-contained under it: the agent's working dir
    # (CLAUDE.md / .mcp.json / recall.jsonl / OKF memory bundle) lands there
    # (stable, not temp), plus a per-run trajectory JSONL and a per-run record JSON.
    workdir = None
    logger = None
    traj_path = rec_path = None
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        workdir = args.out_dir
        traj_path = os.path.join(args.out_dir, f"{benchmark}__{mode}.trajectory.jsonl")
        rec_path = os.path.join(args.out_dir, f"{benchmark}__{mode}.record.json")
        logger = TrajectoryLogger(traj_path, append=False)

    agent = ClaudeCodeAgent(model=args.model, memory_mode=mode, k=args.k,
                            timeout=args.timeout, workdir=workdir)
    cost = CostTracker(budget_usd=args.budget_usd) if args.budget_usd and args.budget_usd > 0 else None
    try:
        rr = run_agent(
            Benchmark.from_str(benchmark), agent, memory=(mode != "off"),
            limit=args.limit, dev_slice=args.dev_slice,
            path_or_id=_resolve_path(benchmark, args.path),
            cost=cost, k=args.k, seed_sessions=False, logger=logger,  # agent seeds memory itself
        )
    except Exception as exc:  # surface per-(benchmark,mode) failure, keep going
        print(f"FAIL {benchmark:18} {mode:8} {type(exc).__name__}: {str(exc)[:140]}")
        return None
    finally:
        if logger is not None:
            logger.close()
    rec = append_result(rr, args.results, run_id=f"claude-code-{mode}",
                        notes=f"Claude Code CLI · memory={mode}")
    if rec_path:
        with open(rec_path, "w", encoding="utf-8") as fh:
            json.dump(result_record(rr, run_id=f"claude-code-{mode}",
                                    notes=f"Claude Code CLI · memory={mode}"), fh, indent=2)
    m = rr.metrics
    print(f"OK   {benchmark:18} {mode:8} acc={m.accuracy:.2f} rel={m.relevancy:.2f} "
          f"rec={m.recency:.2f} eff={m.efficiency:.2f} n={rr.n_tasks} ${rr.cost_usd:.4f}")
    return rec


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="memeval.claudecode.run_bench")
    ap.add_argument("--benchmark", default="all", help="one of the five, or 'all'.")
    ap.add_argument("--mode", default="all", help="builtin | plugin | all (all = builtin+plugin).")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--path", default=None, help="local fixture/dataset path, or 'fixtures' (blank = real source).")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dev-slice", type=float, default=None)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--out-dir", default=None,
                    help="Write per-run raw artifacts here (trajectory JSONL, record JSON, "
                         "and the agent working dir with CLAUDE.md/.mcp.json/recall.jsonl/memory).")
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
