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
# Per-benchmark long-memory floors (entries per (benchmark, mode) on a bare run).
# Tuned to each dataset's real structure (measured via tools/probe_group_sizes.py):
#   longmemeval       500 tasks, 1 group, ~50 sessions/task  -> memory is *within*
#                     each entry; 20 questions is deep + broad enough.
#   memoryagentbench  3671 tasks, 1 group, 1 long-context session/task -> 20 Qs.
#   swe_bench_cl      273 tasks in 8 sequences (size 19-50, median 33); memory is
#                     *across* entries in a sequence, so the floor must cover ~1
#                     full sequence for priors to accumulate -> 33.
#   swe_contextbench  1476 tasks in 280 groups but median group size 1 (singleton-
#                     heavy); only large groups carry memory, so draw wider -> 50
#                     (group-aware selection would be strictly better here).
#   contextbench      1136 tasks, median 9 gold spans/task (in-task retrieval, not
#                     cross-session) -> 20 tasks.
# Override any of these with --limit N (applies to all); --limit 0 = whole dataset.
DEFAULT_FLOORS = {
    "longmemeval": 20,
    "memoryagentbench": 20,
    "swe_bench_cl": 33,
    "swe_contextbench": 50,
    "contextbench": 20,
}
DEFAULT_MIN_ENTRIES = 20  # fallback floor for an unlisted benchmark
# Benchmarks whose memory lives *across* entries in a group_id sequence: draw
# whole groups (largest first) so the limited sample actually carries priors.
# (The QA benches are 1 group / multi-session per entry, so flat == group there.)
_GROUP_AWARE = {"swe_bench_cl", "swe_contextbench"}


def _resolve_path(benchmark: str, path: Optional[str]) -> Optional[str]:
    """``--path fixtures`` -> the bundled per-benchmark fixture; else passthrough."""
    if path == "fixtures":
        from pathlib import Path
        fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / f"{benchmark}.json"
        return str(fx)
    return path


def _resolve_limit(benchmark: str, cli_limit: Optional[int]) -> Optional[int]:
    """Entries to evaluate for ``benchmark``: explicit --limit wins (``0`` = whole
    dataset -> ``None``); otherwise the benchmark's long-memory floor."""
    if cli_limit is None:
        return DEFAULT_FLOORS.get(benchmark, DEFAULT_MIN_ENTRIES)
    return None if cli_limit <= 0 else cli_limit


def _resolve_group_aware(benchmark: str, select: str) -> bool:
    """Whether to use the group-aware draw: ``auto`` -> per-benchmark default
    (on for cross-entry-memory benches); ``group``/``flat`` force it."""
    if select == "group":
        return True
    if select == "flat":
        return False
    return benchmark in _GROUP_AWARE  # auto


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
    limit = _resolve_limit(benchmark, args.limit)
    group_aware = _resolve_group_aware(benchmark, args.select)
    try:
        rr = run_agent(
            Benchmark.from_str(benchmark), agent, memory=(mode != "off"),
            limit=limit, dev_slice=args.dev_slice, group_aware=group_aware,
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
    avail = rr.metadata.get("total_available")
    lim = rr.metadata.get("limit")
    sel = rr.metadata.get("select", "flat")
    entries = f"{rr.n_tasks}/{avail}" if avail is not None else str(rr.n_tasks)
    entries += f" (limit={lim},{sel})" if lim is not None else f" (limit=none,{sel})"
    print(f"OK   {benchmark:18} {mode:8} acc={m.accuracy:.2f} rel={m.relevancy:.2f} "
          f"rec={m.recency:.2f} eff={m.efficiency:.2f} entries={entries} ${rr.cost_usd:.4f}")
    return rec


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="memeval.claudecode.run_bench")
    ap.add_argument("--benchmark", default="all", help="one of the five, or 'all'.")
    ap.add_argument("--mode", default="all", help="builtin | plugin | all (all = builtin+plugin).")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--path", default=None, help="local fixture/dataset path, or 'fixtures' (blank = real source).")
    ap.add_argument("--limit", type=int, default=None,
                    help="dataset entries per (benchmark,mode) run. Default = each "
                         f"benchmark's long-memory floor {DEFAULT_FLOORS}; pass an int to "
                         "override all, or 0 for the whole dataset (no cap).")
    ap.add_argument("--dev-slice", type=float, default=None)
    ap.add_argument("--select", choices=["auto", "flat", "group"], default="auto",
                    help="how to draw the limited sample: 'group' = whole group_id "
                         "groups largest-first (memory-faithful for CL benches), "
                         "'flat' = first-N, 'auto' = per-benchmark default "
                         f"(group for {sorted(_GROUP_AWARE)}).")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--out-dir", default=None,
                    help="Write per-run raw artifacts here (trajectory JSONL, record JSON, "
                         "and the agent working dir with CLAUDE.md/.mcp.json/recall.jsonl/memory).")
    args = ap.parse_args(argv)

    print(describe())   # which CLI was detected (native / WSL / not found)
    print("auth: Claude Code subscription only — ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN "
          "are stripped from every claude invocation (no API billing).")
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
