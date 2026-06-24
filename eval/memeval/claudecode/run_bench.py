"""Run a benchmark (or all five) through the Claude Code CLI and log results.

Installed as the ``memeval-bench`` console command (after ``pip install -e``), so a
developer can run any benchmark on its own through their own Claude Code CLI::

    memeval-bench --benchmark longmemeval --mode plugin --limit 20 --results out.json
    memeval-bench --list-benchmarks          # show the five ids (no claude needed)

The equivalent module form works without installing the entry point::

    python -m memeval.claudecode.run_bench --benchmark longmemeval --mode plugin \
        --model claude-haiku-4-5 --limit 20 --results ../results.json

``--mode`` is off | builtin | plugin | plugin-real | all (all runs builtin+plugin-real
for comparison: Claude Code's native memory vs Keith's shipping cookbook-memory
plugin; the OKF ``plugin`` simulation is explicit opt-in only).
``--benchmark`` is one of the five ids (run it on its own) or ``all``. Reuses the
standard harness (cost/budget, trajectory, metrics, results ledger), so these runs
show up on the Results page / scoreboard next to the API runs.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional

from .. import MEMORY_VERSION
from ..cost import DEFAULT_BUDGET_USD
from ..schema import Benchmark
from .agent import ClaudeCodeAgent
from .platform import describe, detect

_ALL_BENCH = ["memoryagentbench", "longmemeval", "swe_contextbench", "swe_bench_cl", "contextbench", "vista"]
# The comparison that matters: Claude Code's built-in memory (builtin) vs Keith's
# SHIPPING plugin (plugin-real = plugin/cookbook_memory, the real product). The OKF
# `plugin` is a harness SIMULATION of our MCP memory and stays selectable only via an
# explicit `--mode plugin` — it is deliberately NOT in the default `all`, so the
# simulation is never benchmarked by accident in place of the shipping plugin.
# (`off` and `plugin` are still accepted as explicit --mode values, just not in `all`.)
_MODES = ["builtin", "plugin-real"]
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
#   vista             one flat-QA journey per row (drift/injection/slow_burn events
#                     are WITHIN a journey); answer=None, scored by the native
#                     poisoning/calibration/RSI evaluator -> a small floor (6, the
#                     6-journey safety suite) is enough.
# Override any of these with --limit N (applies to all); --limit 0 = whole dataset.
DEFAULT_FLOORS = {
    "longmemeval": 20,
    "memoryagentbench": 20,
    "swe_bench_cl": 33,
    "swe_contextbench": 50,
    "contextbench": 20,
    "vista": 6,
}
DEFAULT_MIN_ENTRIES = 20  # fallback floor for an unlisted benchmark
# Benchmarks whose memory lives *across* entries in a group_id sequence: draw
# whole groups (largest first) so the limited sample actually carries priors.
# (The QA benches are 1 group / multi-session per entry, so flat == group there.)
_GROUP_AWARE = {"swe_bench_cl", "swe_contextbench"}
# CODE-kind benchmarks: tasks carry a gold patch + FAIL_TO_PASS/PASS_TO_PASS.
# The two SWE benches are graded by *applying the patch and running the tests*
# (SWE-bench rule) via the host-local LocalExecGrader (best-effort, ungraded None
# when the env can't be built). contextbench is RETRIEVAL-only: it is scored by its
# native retrieval metric (recall/precision/F1 over gold spans) from the recorded
# retrieve steps, so it needs grader=None (no test execution). The QA benches
# (longmemeval, memoryagentbench) keep grader=None -> exact match.
_CODE_BENCH = {"swe_bench_cl", "swe_contextbench", "contextbench"}
# CODE benches graded by local test execution (a subset of _CODE_BENCH).
_LOCAL_EXEC_BENCH = {"swe_bench_cl", "swe_contextbench"}


def _swebench_available() -> bool:
    """True iff the optional ``swebench`` package can be imported, so ``auto`` can
    prefer :class:`SwebenchHostGrader` and otherwise fall back to LocalExecGrader."""
    import importlib.util

    return importlib.util.find_spec("swebench") is not None


def _make_grader(benchmark: str, args: argparse.Namespace):
    """Resolve the grader for ``benchmark``.

    ``auto`` (default): QA benches and contextbench get ``None`` (exact-match /
    native retrieval metric — no test execution); the two SWE benches get the
    realistic :class:`SwebenchHostGrader` **when the optional ``swebench`` package
    is importable**, else fall back to the host-local :class:`LocalExecGrader`
    (so a host without the extra still grades, just with the heuristic env). ``none``
    -> ``None``; ``local`` -> LocalExecGrader for any bench; ``swebench`` -> force the
    SWE-bench-spec grader (errors if the extra is absent); ``overlap`` -> the cheap
    heuristic. The grader returns ``None`` for non-CODE tasks, so an explicit choice
    is safe on a QA bench; both CODE graders degrade to ``None`` (ungraded) when the
    env can't be built, so a missing toolchain leaves CODE tasks ungraded, not crashing.
    """
    from ..grader import get_grader

    choice = (args.grader or "auto").strip().lower()
    if choice == "auto":
        if benchmark in _LOCAL_EXEC_BENCH:
            # Prefer the SWE-bench-spec grader (official per-instance python/install/
            # test specs + log parsers) when its optional package is installed; fall
            # back to the heuristic LocalExecGrader otherwise so grading never breaks
            # on a host that lacks the extra.
            name = "swebench" if _swebench_available() else "local"
            return get_grader(name, timeout=args.grader_timeout)
        return None  # QA benches + contextbench (retrieval-only): native metric
    if choice == "none":
        return None
    if choice in ("local", "localexec", "local-exec"):
        return get_grader("local", timeout=args.grader_timeout)
    if choice in ("swebench", "swebench-host", "swebenchhost"):
        # The realistic Docker-free grader also honors --grader-timeout (it builds a
        # per-instance venv + runs the repo's tests, which can be slow on old pins).
        return get_grader(choice, timeout=args.grader_timeout)
    return get_grader(choice)  # overlap (or any other registered name)


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


# Benchmarks whose run we additionally score through their NATIVE evaluator and
# attach as ``rr.metadata["native_report"]`` so results.result_record emits the
# row's ``native`` block (rendered on results.html). VISTA is the one that turns
# a plain plugin-real run into a real memory-SAFETY measurement (poisoning
# resistance, targeted ASR, retrieval P/R/F1, adaptation rate, RSI
# self_improvement_safety). Ken-owned reporting glue: it reads the trajectories
# the harness already recorded and the loader's tasks; it never touches the
# team-owned plugin/dreaming/stores code.
_NATIVE_REPORT_BENCH = {"vista"}


def _attach_native_report(rr: Any, benchmark: str, mode: str,
                          tasks: Optional[list] = None) -> None:
    """Score ``rr``'s recorded trajectories through ``benchmark``'s native evaluator
    and stash the report on ``rr.metadata['native_report']`` (where
    :func:`memeval.results.result_record` looks for it).

    Robust by design: the native ``score`` reads each trajectory's ``retrieve``
    steps (item ids + text) and the task's gold/oracle fields — the EXACT shape a
    plugin-real run already produces (``_attribute_real_recall`` records retrieve
    steps; the VISTA loader carries route_graph/oracle_bindings on the task). We
    adapt by joining the run's trajectories to freshly-loaded tasks (by task_id)
    and wrapping each trajectory in a :class:`PerTaskRecord`. If anything is
    missing or the evaluator/loader is unavailable, we log a clear warning and
    leave the run unannotated rather than crash the benchmark."""
    if benchmark not in _NATIVE_REPORT_BENCH:
        return
    try:
        from ..native.registry import get_native_evaluator
        from ..native.spec import PerTaskRecord
        from ..schema import Benchmark as _B

        if tasks is None:
            from ..loaders import get_loader
            loader = get_loader(_B.from_str(benchmark))
            tasks = loader.load(None)
        by_id = {t.task_id: t for t in tasks}
        trajs = list(getattr(rr, "trajectories", []) or [])
        if not trajs:
            print(f"     native[{benchmark}]: no trajectories to score — skipped",
                  file=sys.stderr)
            return
        # Score only trajectories whose task we can join (the loader is the source
        # of the gold/oracle fields the evaluator needs).
        records = [PerTaskRecord.from_trajectory(t) for t in trajs if t.task_id in by_id]
        if not records:
            print(f"     native[{benchmark}]: no trajectory joined a loaded task — "
                  f"skipped (ran {len(trajs)} tasks)", file=sys.stderr)
            return
        evaluator = get_native_evaluator(benchmark)
        report = evaluator.score(records, tasks)
        report.mode = mode  # reflect the real run mode (plugin-real), not the default
        rr.metadata["native_report"] = report
    except Exception as exc:  # reporting must never abort a real run
        print(f"     native[{benchmark}]: could not attach native report "
              f"({type(exc).__name__}: {str(exc)[:120]}) — run still recorded",
              file=sys.stderr)


def _run_one(benchmark: str, mode: str, args: argparse.Namespace,
             *, stamp: str = "", completed_recs: Optional[list] = None) -> Optional[dict]:
    import json
    import os
    from ..agent import run_agent
    from ..cost import CostTracker
    from ..results import append_result, result_record, write_benchmark_results
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

    # plugin-real memory must persist ACROSS tasks for a continual-learning benchmark,
    # or each task gets a fresh empty store and "improvement over time" can never show
    # (every recall returns 0 hits). Point CLAUDE_PROJECT_DIR at a shared substrate under
    # the out-dir, keyed per sequence (group_id) by the agent, so a sequence accumulates
    # while different sequences stay isolated. Without --out-dir there's no persistent
    # home, so we leave it per-task (unchanged). off/builtin/plugin are unaffected.
    substrate = (os.path.join(args.out_dir, "_memory")
                 if (mode == "plugin-real" and args.out_dir) else None)
    agent = ClaudeCodeAgent(model=args.model, memory_mode=mode, k=args.k,
                            timeout=args.timeout, workdir=workdir,
                            code_mode=args.code_mode,
                            project_dir=substrate, group_scoped_store=substrate is not None)
    cost = CostTracker(budget_usd=args.budget_usd) if args.budget_usd and args.budget_usd > 0 else None
    limit = _resolve_limit(benchmark, args.limit)
    group_aware = _resolve_group_aware(benchmark, args.select)
    grader = _make_grader(benchmark, args)
    run_id = f"claude-code-{mode}"
    notes = f"Claude Code CLI · memory={mode}"
    # Plugin talks to an MCP server; headless claude's MCP connection degrades under
    # concurrency, so plugin runs at --plugin-workers (default 1) while builtin/off
    # (no MCP, just file reads) run at the full --workers.
    workers = args.plugin_workers if mode in ("plugin", "plugin-real") else args.workers

    # Incremental save: after every task, rewrite this benchmark's result file with
    # the records from already-finished modes plus the current mode's partial run,
    # so a crash mid-run still leaves a valid results/v{X.Y}/{bench}-{stamp}.json.
    prior_recs = list(completed_recs or [])
    progress_cb = None
    if args.results_dir and stamp:
        def progress_cb(partial_rr: Any) -> None:
            prec = result_record(partial_rr, run_id=run_id, notes=notes)
            write_benchmark_results(benchmark, prior_recs + [prec],
                                    version=args.results_version, timestamp=stamp,
                                    root=args.results_dir)
            if rec_path:
                with open(rec_path, "w", encoding="utf-8") as fh:
                    json.dump(prec, fh, indent=2)

    try:
        rr = run_agent(
            Benchmark.from_str(benchmark), agent, memory=(mode != "off"),
            limit=limit, dev_slice=args.dev_slice, group_aware=group_aware,
            path_or_id=_resolve_path(benchmark, args.path),
            cost=cost, k=args.k, seed_sessions=False, logger=logger,  # agent seeds memory itself
            workers=workers, progress_cb=progress_cb, grader=grader,
        )
    except Exception as exc:  # surface per-(benchmark,mode) failure, keep going
        print(f"FAIL {benchmark:18} {mode:8} {type(exc).__name__}: {str(exc)[:140]}")
        return None
    finally:
        if logger is not None:
            logger.close()
    # Surface the benchmark's NATIVE metrics on the REAL trajectories this run just
    # recorded (VISTA: poisoning/calibration/RSI safety). Attaches a native_report
    # to rr.metadata so result_record/append_result emit the row's `native` block.
    _attach_native_report(rr, benchmark, mode)
    rec = append_result(rr, args.results, run_id=run_id, notes=notes)
    if rec_path:
        with open(rec_path, "w", encoding="utf-8") as fh:
            json.dump(result_record(rr, run_id=run_id, notes=notes), fh, indent=2)
    m = rr.metrics
    avail = rr.metadata.get("total_available")
    lim = rr.metadata.get("limit")
    sel = rr.metadata.get("select", "flat")
    entries = f"{rr.n_tasks}/{avail}" if avail is not None else str(rr.n_tasks)
    entries += f" (limit={lim},{sel})" if lim is not None else f" (limit=none,{sel})"
    print(f"OK   {benchmark:18} {mode:8} acc={m.accuracy:.2f} rel={m.relevancy:.2f} "
          f"rec={m.recency:.2f} eff={m.efficiency:.2f} entries={entries} ${rr.cost_usd:.4f}")
    return rec


def _benchmark_table() -> str:
    """One line per benchmark: id, kind (QA/CODE), default floor, draw — for --list."""
    rows = ["available benchmarks (run any one on its own with --benchmark <id>):"]
    for b in _ALL_BENCH:
        kind = "CODE" if b in _CODE_BENCH else "QA"
        draw = "group" if b in _GROUP_AWARE else "flat"
        floor = DEFAULT_FLOORS.get(b, DEFAULT_MIN_ENTRIES)
        rows.append(f"  {b:<18} {kind:<4} floor={floor:<3} draw={draw}")
    rows.append("  all                run every benchmark in sequence")
    rows.append("modes: builtin (Claude Code's native memory) | plugin-real (Keith's "
                "shipping cookbook_memory plugin — the REAL product, natively installed, "
                "black box) | plugin (OKF/MCP harness SIMULATION — explicit opt-in only, "
                "NOT in 'all') | off (baseline) | all (builtin+plugin-real)")
    return "\n".join(rows)


def _openrouter_advisory(modes: list[str]) -> Optional[str]:
    """NON-fatal advisory when ``plugin-real`` runs without ``OPENROUTER_API_KEY``.

    plugin-real does NOT depend on OpenRouter to run: memory is seeded through the
    plugin's own write surface (``memory-cli remember`` — the user/Daydreamer
    surface), and ``recall`` works regardless. OpenRouter only powers the plugin's
    *dream/Daydreamer* consolidation, which fail-opens to a no-op when the key is
    unset (ADR-dreaming-012). So the bench still runs on the seeded (or empty)
    memory — this only flags that the *dream lift* won't appear until you set the
    key and re-run to compare. Returns the advisory string, or ``None`` when N/A.

    Intentionally advisory, not blocking: the empty-memory -> dream-inserts ->
    re-run -> compare workflow must be allowed to start.
    """
    if "plugin-real" in modes and not (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        return ("note: OPENROUTER_API_KEY unset — plugin-real runs on seeded memory "
                "only; the dream/Daydreamer consolidation is a no-op (ADR-dreaming-012). "
                "Set it in `.env` or export it, then re-run to compare the dream lift.")
    return None


def main(argv: Optional[list[str]] = None) -> int:
    from ..dotenv_loader import load_root_dotenv
    load_root_dotenv()  # OPENROUTER_API_KEY etc. from the repo-root .env (export still wins)
    ap = argparse.ArgumentParser(
        prog="memeval-bench",
        description="Run a memory benchmark (or all five) through your Claude Code CLI, "
                    "comparing Claude Code's built-in memory (builtin) vs Keith's "
                    "shipping cookbook_memory plugin (plugin-real, the real product). "
                    "The OKF `plugin` harness simulation is explicit opt-in only. "
                    "Subscription auth only (no API key).",
    )
    ap.add_argument("--list-benchmarks", action="store_true",
                    help="List the available benchmark ids (with kind/floor/draw) and exit. "
                         "Works offline — no claude or dataset needed.")
    ap.add_argument("--benchmark", default="all", help="one of the five ids, or 'all'.")
    ap.add_argument("--mode", default="all",
                    help="builtin (Claude Code native) | plugin-real (Keith's shipping "
                         "cookbook_memory plugin, the real product) | plugin (OKF/MCP "
                         "harness simulation — explicit opt-in only) | off (baseline) | "
                         "all (= builtin+plugin-real).")
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
    ap.add_argument("--workers", type=int, default=4,
                    help="Concurrent `claude` CLI processes for builtin/off runs "
                         "(task-level parallelism; default 4, 1 = sequential).")
    ap.add_argument("--plugin-workers", type=int, default=1,
                    help="Concurrency for PLUGIN runs (default 1). Plugin uses an MCP "
                         "server whose headless connection degrades under concurrency, "
                         "so it runs sequentially by default for reliable retrieval.")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--code-mode", choices=["blind", "agentic"], default="agentic",
                    help="How CODE tasks are solved: 'agentic' (default) drives "
                         "claude as a real coding agent in a working checkout (it "
                         "edits files + runs tests; `git diff` is the prediction); "
                         "'blind' asks for a unified diff in one turn (no checkout).")
    ap.add_argument("--grader", default="auto",
                    help="CODE-task grader: 'auto' (default) = local test execution "
                         f"for {sorted(_LOCAL_EXEC_BENCH)}, None for QA and contextbench "
                         "(retrieval-only, scored by its native metric); 'local' = "
                         "host-local per-task venv exec (best-effort; ungraded None "
                         "when the env can't be built); 'swebench' = Docker-free grader "
                         "reusing SWE-bench's own specs + log parsers (needs the "
                         "'swebench' extra; honors --grader-timeout); 'overlap' = cheap "
                         "gold-patch token-overlap heuristic (NOT real accuracy); 'none' "
                         "= leave CODE ungraded.")
    ap.add_argument("--grader-timeout", type=int, default=1800,
                    help="Per-task local test-execution timeout (seconds).")
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--results-dir", default="results",
                    help="Root for the per-benchmark result files written as "
                         "{results-dir}/v{X.Y}/{benchmark}-{timestamp}.json (one file per "
                         "benchmark, holding its runs). Pass '' to skip.")
    ap.add_argument("--results-version", default=MEMORY_VERSION,
                    help=f"Memory-system version bucket for --results-dir (default "
                         f"v{MEMORY_VERSION}). Bump 0.1 per memory change + run.")
    ap.add_argument("--out-dir", default=None,
                    help="Write per-run raw artifacts here (trajectory JSONL, record JSON, "
                         "and the agent working dir with CLAUDE.md/.mcp.json/recall.jsonl/memory).")
    args = ap.parse_args(argv)

    # --list-benchmarks is a pure-offline discovery aid: print and exit before we
    # probe for the claude CLI or touch any dataset.
    if args.list_benchmarks:
        print(_benchmark_table())
        return 0

    # Validate the benchmark id up front so a single-benchmark run fails fast with a
    # helpful message instead of deep inside the loader.
    if args.benchmark != "all" and args.benchmark not in _ALL_BENCH:
        ap.error(f"unknown --benchmark {args.benchmark!r}; choose one of "
                 f"{_ALL_BENCH} or 'all' (see --list-benchmarks).")

    print(describe())   # which CLI was detected (native / WSL / not found)
    print("auth: Claude Code subscription only — ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN "
          "are stripped from every claude invocation (no API billing).")
    if detect() is None:
        print("WARNING: runs will fail until 'claude' is installed (native or in WSL). "
              "`npm install -g @anthropic-ai/claude-code`; overrides: $CLAUDE_CLI / $CLAUDE_WSL_DISTRO.")

    benches = _ALL_BENCH if args.benchmark == "all" else [args.benchmark]
    modes = _MODES if args.mode == "all" else [args.mode]

    # Advisory only (NON-blocking): plugin-real runs fine without OPENROUTER_API_KEY
    # — memory is seeded via the plugin's own memory-cli; OpenRouter only powers the
    # dream consolidation, which fail-opens (ADR-dreaming-012). Surface the note so a
    # teammate knows the dream lift needs the key, but never block the run.
    _or_note = _openrouter_advisory(modes)
    if _or_note is not None:
        print(_or_note, file=sys.stderr)

    # One timestamp for the whole sweep, so a sweep's per-benchmark files share it:
    # results/v{X.Y}/{benchmark}-{timestamp}.json
    from ..results import run_timestamp, write_benchmark_results
    stamp = run_timestamp()

    n_ok = 0
    for b in benches:
        recs = []
        for mode in modes:
            rec = _run_one(b, mode, args, stamp=stamp, completed_recs=recs)
            if rec is not None:
                n_ok += 1
                recs.append(rec)
        if recs and args.results_dir:
            path = write_benchmark_results(
                b, recs, version=args.results_version, timestamp=stamp, root=args.results_dir)
            print(f"     -> {path}")

    print(f"\n{n_ok}/{len(benches) * len(modes)} run(s) logged -> {args.results}")
    if args.results_dir:
        from ..results import normalize_version
        print(f"Per-benchmark results: {args.results_dir}/{normalize_version(args.results_version)}/"
              f"<benchmark>-{stamp}.json")
    print("Scoreboard:  python -m memeval.results summary --path " + args.results)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
