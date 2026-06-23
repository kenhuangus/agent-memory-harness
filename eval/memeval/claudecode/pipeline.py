"""The 5-stage SWE-Bench-CL pipeline driven by the live cookbook-memory plugin.

Installed as ``memeval-pipeline`` (and ``python -m memeval.claudecode.pipeline``). Runs,
over the same X tasks of ONE named SWE-Bench-CL sequence, five stages that together test
whether an accumulating + dream-consolidated memory makes the agent get better over time:

  1. base          -- mode=off, no plugin (the baseline)
  2. plugin-blank   -- plugin-real, empty shared memory substrate
  3. plugin-accum   -- plugin-real, the SAME substrate (now holding stage-2's memory)
  4. dream          -- the plugin's own ``daydream-cli dream`` over the substrate
  5. plugin-dreamed -- plugin-real, the SAME substrate after the dream pass (final)

Memory is ONE shared substrate per pipeline VERSION at ``results/v{version}/_memory/``
(ADR-eval-003): the harness only ensures that directory exists and points
``CLAUDE_PROJECT_DIR`` at it; the plugin owns everything inside. Accumulation across
stages 2->3->5 happens purely because the directory persists -- the harness never copies,
seeds, or prunes the store. The version is the git tag on HEAD (ADR-eval-004).

The wrapper is interactive by default (offer + confirm the defaults) with a
non-interactive ``--yes`` mode for CI/scripts. Drives the SAME machinery the per-dev
``memeval-bench`` uses (``ClaudeCodeAgent`` + ``run_agent`` + ``LocalExecGrader``).
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path
from typing import Any, Optional

from .. import MEMORY_VERSION
from ..cost import DEFAULT_BUDGET_USD
from ..schema import Benchmark

_BENCHMARK = "swe_bench_cl"

#: The 8 SWE-Bench-CL sequences (the "domains"), largest first; sizes for the prompt.
_SEQUENCES = {
    "django_django_sequence": 50,
    "sympy_sympy_sequence": 50,
    "sphinx-doc_sphinx_sequence": 44,
    "matplotlib_matplotlib_sequence": 34,
    "scikit-learn_scikit-learn_sequence": 32,
    "astropy_astropy_sequence": 22,
    "pydata_xarray_sequence": 22,
    "pytest-dev_pytest_sequence": 19,
}
_DEFAULT_SEQUENCE = "pytest-dev_pytest_sequence"  # smallest -> cheapest to iterate
_DEFAULT_LIMIT = 20

#: The eval stages (the dream stage runs between accum and dreamed; it is not an eval).
_EVAL_STAGES = ("base", "plugin-blank", "plugin-accum", "plugin-dreamed")
_STAGE_INDEX = {"base": 1, "plugin-blank": 2, "plugin-accum": 3, "plugin-dreamed": 5}
_STAGE_MODE = {
    "base": "off",
    "plugin-blank": "plugin-real",
    "plugin-accum": "plugin-real",
    "plugin-dreamed": "plugin-real",
}


# --------------------------------------------------------------------------- #
# Config resolution + interactive wrapper
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="memeval-pipeline",
        description="Run the 5-stage SWE-Bench-CL pipeline against the live cookbook-memory "
                    "plugin: base -> plugin/blank -> plugin/accumulated -> dream -> "
                    "plugin/dreamed, sharing ONE persistent per-version memory substrate, "
                    "then write a base->final summary.",
    )
    ap.add_argument("-y", "--yes", "--non-interactive", dest="yes", action="store_true",
                    help="Non-interactive: use flags where given, defaults otherwise; no prompts.")
    ap.add_argument("--sequence", default=None,
                    help=f"SWE-Bench-CL sequence (the Y domain). One of: "
                         f"{', '.join(_SEQUENCES)}. Default {_DEFAULT_SEQUENCE}.")
    ap.add_argument("--limit", type=int, default=None,
                    help=f"How many tasks of the sequence to run (by Task.order). "
                         f"Default {_DEFAULT_LIMIT}; 0 = the whole sequence.")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--code-mode", choices=["blind", "agentic"], default="agentic")
    ap.add_argument("--grader", default="local",
                    help="CODE grader: 'local' (host test execution; the real resolve "
                         "rate), 'overlap' (cheap heuristic), or 'none'.")
    ap.add_argument("--grader-timeout", type=int, default=1800)
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--plugin-workers", type=int, default=1,
                    help="Concurrency for plugin stages (default 1; the plugin MCP "
                         "connection degrades under headless concurrency).")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--path", default=None, help="Dataset path/id (blank = real source).")
    ap.add_argument("--results-dir", default="results",
                    help="Root for results/v{version}/ (and the shared _memory/ substrate).")
    ap.add_argument("--native-cl", dest="native_cl", action="store_true", default=True,
                    help="Capture paper-native CL metrics per eval stage (default on).")
    ap.add_argument("--no-native-cl", dest="native_cl", action="store_false")
    return ap


def _prompt(label: str, default: Any) -> str:
    """One interactive prompt with a default (Enter accepts it). Falls through to the
    default on a non-tty (so a piped/CI invocation without --yes still works)."""
    if not sys.stdin or not sys.stdin.isatty():
        return str(default)
    raw = input(f"  {label} [{default}]: ").strip()
    return raw or str(default)


def _resolve_config(args: argparse.Namespace) -> dict:
    """Resolve the run config from flags + (when interactive) prompts. Any flag passed
    explicitly pre-fills its prompt default; --yes skips all prompts."""
    seq = args.sequence or _DEFAULT_SEQUENCE
    limit = _DEFAULT_LIMIT if args.limit is None else args.limit
    model = args.model
    grader = args.grader
    budget = args.budget_usd

    if not args.yes:
        print("Configure the 5-stage SWE-Bench-CL pipeline (Enter accepts the default):")
        print(f"  sequences: {', '.join(f'{k}({v})' for k, v in _SEQUENCES.items())}")
        seq = _prompt("sequence", seq)
        limit = int(_prompt("limit (0 = whole sequence)", limit))
        model = _prompt("model", model)
        grader = _prompt("grader (local|overlap|none)", grader)
        budget = float(_prompt("budget-usd", budget))

    if seq not in _SEQUENCES:
        raise SystemExit(f"unknown --sequence {seq!r}; choose one of {list(_SEQUENCES)}")

    return {
        "sequence": seq,
        "limit": None if int(limit) <= 0 else int(limit),
        "model": model,
        "grader": grader,
        "budget_usd": budget,
        "code_mode": args.code_mode,
        "grader_timeout": args.grader_timeout,
        "plugin_workers": args.plugin_workers,
        "timeout": args.timeout,
        "path": args.path,
        "results_dir": args.results_dir,
        "native_cl": args.native_cl,
    }


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def _dream_meta() -> dict:
    """The dreamer (subconscious) model recorded for provenance (ADR-dreaming-004)."""
    return {
        "provider": os.environ.get("DREAM_PROVIDER", "openrouter"),
        "model": os.environ.get("DREAM_MODEL", "inclusionai/ling-2.6-flash"),
    }


def _pipeline_meta(cfg: dict, version_info: dict, substrate: Path, stamp: str) -> dict:
    return {
        "version": version_info["version"],
        "version_exact": version_info.get("version_exact"),
        "untagged": version_info.get("untagged"),
        "git_sha": version_info.get("git_sha", ""),
        "sequence": cfg["sequence"],          # the Y domain -- NOT in memory anymore
        "limit": cfg["limit"],
        "n_tasks": None,                       # filled from the first stage's actual count
        "model": cfg["model"],
        "code_mode": cfg["code_mode"],
        "grader": cfg["grader"],
        "plugin_workers": cfg["plugin_workers"],
        "budget_usd": cfg["budget_usd"],
        "dream": _dream_meta(),
        "memory_store": str(substrate),
        "n_stages": 5,
        "n_eval_stages": len(_EVAL_STAGES),
        "stages": ["base", "plugin-blank", "plugin-accum", "dream", "plugin-dreamed"],
        "timestamp": stamp,
        "started_at": None,                    # epoch seconds, set when the run starts
        "ended_at": None,                      # epoch seconds, set when the run ends
    }


def _make_agent(stage: str, cfg: dict, substrate: Path):
    """Build the ClaudeCodeAgent for a stage. Plugin stages share the ONE substrate
    (project_dir); the base stage has no memory."""
    from .agent import ClaudeCodeAgent

    mode = _STAGE_MODE[stage]
    return ClaudeCodeAgent(
        model=cfg["model"], memory_mode=mode, code_mode=cfg["code_mode"],
        timeout=cfg["timeout"],
        project_dir=substrate if mode == "plugin-real" else None,
    )


def _grader(cfg: dict):
    """Resolve the CODE grader, reusing run_bench's resolver via a tiny args shim."""
    from .run_bench import _make_grader
    shim = types.SimpleNamespace(grader=cfg["grader"], grader_timeout=cfg["grader_timeout"])
    return _make_grader(_BENCHMARK, shim)


def _run_eval_stage(stage: str, cfg: dict, substrate: Path, *, cost: Any) -> Any:
    """Run one eval stage through ``run_agent`` (the same machinery memeval-bench uses)
    and return its ``RunResult``."""
    from ..agent import run_agent

    agent = _make_agent(stage, cfg, substrate)
    workers = cfg["plugin_workers"] if _STAGE_MODE[stage] == "plugin-real" else 1
    return run_agent(
        Benchmark.from_str(_BENCHMARK), agent,
        memory=(_STAGE_MODE[stage] != "off"),
        limit=cfg["limit"], sequence=cfg["sequence"],
        path_or_id=cfg["path"], cost=cost, grader=_grader(cfg),
        seed_sessions=False, workers=workers,
    )


def _native_cl_for_stage(stage: str, cfg: dict, substrate: Path) -> Optional[dict]:
    """Compute the paper-native CL report for an eval stage and return its dict.

    NOTE: the native evaluator runs its OWN mem-on / re-test / mem-off A/B over the
    sequence, and assumes a per-sequence memory reset -- which the shared accumulating
    substrate deliberately breaks. The report is captured for comparison and flagged in
    the summary; it is not the pipeline's primary metric (resolve-rate accuracy is)."""
    from ..loaders import get_loader
    from ..native.registry import get_native_evaluator

    bench = Benchmark.from_str(_BENCHMARK)
    tasks = [t for t in get_loader(bench).load(cfg["path"], limit=None)
             if str(t.group_id or "") == cfg["sequence"]]
    tasks.sort(key=lambda t: int(t.order))
    if cfg["limit"]:
        tasks = tasks[: cfg["limit"]]
    if not tasks:
        return None
    evaluator = get_native_evaluator(bench)
    agent = _make_agent(stage, cfg, substrate)
    records = evaluator.run(tasks, agent_or_model=agent, mode=_STAGE_MODE[stage],
                            grader=cfg["grader"])
    report = evaluator.score(records, tasks)
    d = report.to_dict()
    d.setdefault("metadata", {})["caveat"] = (
        "native CL assumes per-sequence memory reset; the pipeline's shared substrate "
        "accumulates across stages, so these are comparative, not paper-faithful"
    )
    return d


def _run_dream_stage(substrate: Path) -> dict:
    """Dream stage -- a NO-OP placeholder until consolidation is actually implemented.

    Whole-store consolidation ("night dream") is not implemented (the v1 dreaming worker
    is detection-only and mutation is gated -- ADR-dreaming-020). The pipeline keeps this
    stage as a structural slot so the 5-stage shape and the base->final comparison are in
    place, but it does NOT touch the shared substrate: no subprocess, no store read, no
    side effects. When real consolidation lands behind the plugin's own surface, this stage
    invokes it (and ONLY through that surface); for now stage 5 runs on the same substrate
    stage 3 left, so the stage-3->5 delta is expected to be ~0.

    ``substrate`` is accepted for signature stability but intentionally unused."""
    return {"status": "not-implemented",
            "note": "whole-store dream consolidation is not implemented yet "
                    "(ADR-dreaming-020); this stage is a no-op placeholder",
            "dream_consolidation": "not-implemented (no-op)"}


def run_pipeline(cfg: dict) -> dict:
    """Run all 5 stages and write the results + summary. Returns the summary dict."""
    from ..cost import CostTracker
    from ..results import resolve_pipeline_version, run_timestamp
    from . import pipeline_summary as PS

    import time

    version_info = resolve_pipeline_version()
    version = version_info["version"]
    stamp = run_timestamp()
    results_root = Path(cfg["results_dir"])
    substrate = (results_root / version / "_memory").resolve()
    substrate.mkdir(parents=True, exist_ok=True)  # the harness's ONLY store responsibility

    meta = _pipeline_meta(cfg, version_info, substrate, stamp)
    meta["started_at"] = time.time()
    print(f"pipeline v{version.lstrip('v')} · sequence {cfg['sequence']} · "
          f"limit {cfg['limit']} · model {cfg['model']}")
    print(f"shared memory substrate: {substrate}")
    if version_info.get("untagged"):
        print("NOTE: HEAD is untagged -> version fell back to MEMORY_VERSION "
              f"(v{MEMORY_VERSION}); tag the commit for a comparable run.", file=sys.stderr)

    rows: list[dict] = []
    native_by_stage: dict[str, dict] = {}
    dream: Optional[dict] = None

    def _write_partial() -> None:
        PS.write_pipeline_results(benchmark=_BENCHMARK, version=version, timestamp=stamp,
                                  rows=rows, pipeline_meta=meta, dream=dream,
                                  root=str(results_root))

    cost = CostTracker(budget_usd=cfg["budget_usd"]) if cfg["budget_usd"] and cfg["budget_usd"] > 0 else None

    for stage in ("base", "plugin-blank", "plugin-accum"):
        row = _run_one(stage, cfg, substrate, cost, native_by_stage, meta)
        rows.append(row)
        # Record the actual task count once (the substrate no longer records the run).
        if meta.get("n_tasks") is None:
            meta["n_tasks"] = row.get("n_tasks")
        _write_partial()

    # Stage 4: dream consolidation through the plugin's own surface.
    print("stage 4 (dream): no-op placeholder — consolidation not implemented yet")
    dream = _run_dream_stage(substrate)
    _write_partial()

    # Stage 5: final eval on the dream-consolidated substrate.
    rows.append(_run_one("plugin-dreamed", cfg, substrate, cost, native_by_stage, meta))
    meta["ended_at"] = time.time()

    path = PS.write_pipeline_results(benchmark=_BENCHMARK, version=version, timestamp=stamp,
                                     rows=rows, pipeline_meta=meta, dream=dream,
                                     root=str(results_root))
    summary = PS.build_summary(benchmark=_BENCHMARK, rows=rows, pipeline_meta=meta,
                               dream=dream, native_by_stage=native_by_stage)
    md_path, json_path = PS.write_summary(benchmark=_BENCHMARK, version=version,
                                          timestamp=stamp, summary=summary,
                                          root=str(results_root))
    print(f"\nresults: {path}")
    print(f"summary: {md_path}")
    print(PS.render_summary_md(summary))
    return summary


def _run_one(stage: str, cfg: dict, substrate: Path, cost: Any,
             native_by_stage: dict, meta: dict) -> dict:
    """Run one eval stage (standard metrics + optional native CL) and return its row."""
    from . import pipeline_summary as PS

    idx = _STAGE_INDEX[stage]
    print(f"stage {idx} ({stage}): mode={_STAGE_MODE[stage]}")
    rr = _run_eval_stage(stage, cfg, substrate, cost=cost)
    m = rr.metrics
    print(f"  acc={m.accuracy:.3f} rel={m.relevancy:.3f} rec={m.recency:.3f} "
          f"eff={m.efficiency:.3f} n={rr.n_tasks} ${rr.cost_usd:.4f}")
    if cfg["native_cl"]:
        try:
            report = _native_cl_for_stage(stage, cfg, substrate)
            if report is not None:
                native_by_stage[stage] = report
        except Exception as exc:  # native CL is supplementary -- never abort the stage
            print(f"  native CL skipped: {type(exc).__name__}: {str(exc)[:120]}",
                  file=sys.stderr)
    return PS.stage_row(rr, stage=stage, stage_index=idx, pipeline_meta=meta)


def main(argv: Optional[list[str]] = None) -> int:
    from .platform import describe, detect

    args = _build_parser().parse_args(argv)
    cfg = _resolve_config(args)

    print(describe())
    if detect() is None:
        print("WARNING: 'claude' not found — plugin stages will fail until it is installed "
              "(npm install -g @anthropic-ai/claude-code).", file=sys.stderr)

    if not args.yes:
        ans = _prompt("run these 5 stages now? (y/n)", "y").lower()
        if ans not in ("y", "yes"):
            print("aborted.")
            return 0

    run_pipeline(cfg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
