"""Run-results ledger -> the GitHub Pages **Results** page.

Every benchmark run can be appended to a small JSON ledger (the repo-root
``results.json``) that the static Results page (``results.html``) fetches and
renders. That is how a run becomes visible on the site:

    run  ->  append_result(run_result, "results.json")  ->  commit (PR)  ->  Pages shows it

Stdlib-only. Logging timestamps use wall-clock, which is **metadata only** and
never enters the deterministic metric path.

Programmatic (the main path — log right after a run)::

    from memeval.harness import run
    from memeval.results import append_result
    rr = run(Benchmark.LONGMEMEVAL, model, memory=True, path_or_id=...)
    append_result(rr, "results.json", run_id="2026-06-18-haiku-mem")

CLI (run + log in one shot, then `show`)::

    python -m memeval.results run --benchmark longmemeval --model echo \
        --no-memory --path tests/fixtures/longmemeval.json --results ../results.json
    python -m memeval.results show --path ../results.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .cost import DEFAULT_BUDGET_USD
from .schema import RunResult

#: Bump if the ledger record shape changes (the Results page checks this).
SCHEMA_VERSION = 1
#: Default ledger path (repo-root file the Results page fetches).
DEFAULT_PATH = "results.json"


def _now_iso() -> str:
    """Current UTC time, ISO-8601 (seconds). Metadata only — not metric logic."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def result_record(
    rr: RunResult,
    *,
    run_id: str = "",
    timestamp: Optional[str] = None,
    notes: str = "",
    extra: Optional[dict] = None,
) -> dict:
    """Flatten a :class:`~memeval.schema.RunResult` into one ledger row.

    The row is JSON-serializable and self-contained: benchmark, model, the
    memory flag, the four metrics (+ extras), task count, cost/tokens, and the
    partial/budget flags. ``extra`` merges in any caller fields (e.g. a git sha).
    """
    cfg = rr.config
    rec: dict[str, Any] = {
        "run_id": run_id,
        "timestamp": timestamp or _now_iso(),
        "benchmark": rr.benchmark.value,
        "model": cfg.name,
        "memory": bool(cfg.memory),
        "label": cfg.label,
        "mode": rr.metadata.get("mode", "single"),
        "metrics": rr.metrics.to_dict(),
        "n_tasks": rr.n_tasks,
        "cost_usd": round(rr.cost_usd, 6),
        "tokens_in": rr.tokens_in,
        "tokens_out": rr.tokens_out,
        "partial": rr.partial,
        "budget_exceeded": rr.budget_exceeded,
        "source": rr.metadata.get("source", ""),
        "notes": notes,
    }
    if extra:
        rec.update(extra)
    return rec


def load_results(path: "str | Path" = DEFAULT_PATH) -> dict:
    """Load the ledger (``{"schema", "updated", "runs": [...]}``) or a fresh one."""
    p = Path(path)
    if not p.is_file():
        return {"schema": SCHEMA_VERSION, "updated": "", "runs": []}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {"runs": data if isinstance(data, list) else []}
    data.setdefault("schema", SCHEMA_VERSION)
    data.setdefault("runs", [])
    return data


def append_record(rec: dict, path: "str | Path" = DEFAULT_PATH) -> dict:
    """Append a pre-built row to the ledger and write it back (pretty JSON)."""
    data = load_results(path)
    data["runs"].append(rec)
    data["updated"] = _now_iso()
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return rec


def append_result(rr: RunResult, path: "str | Path" = DEFAULT_PATH, **kwargs: Any) -> dict:
    """Append one :class:`RunResult` to the ledger. Returns the written row.

    ``kwargs`` are forwarded to :func:`result_record` (``run_id``, ``timestamp``,
    ``notes``, ``extra``).
    """
    return append_record(result_record(rr, **kwargs), path)


# --------------------------------------------------------------------------- #
# CLI: run + log, or show the ledger
# --------------------------------------------------------------------------- #
def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="memeval.results",
        description="Run a benchmark and log it to the Results-page ledger, or show the ledger.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Run one benchmark and append the result to the ledger.")
    r.add_argument("--benchmark", required=True)
    r.add_argument("--model", default="echo")
    mem = r.add_mutually_exclusive_group()
    mem.add_argument("--memory", dest="memory", action="store_true")
    mem.add_argument("--no-memory", dest="memory", action="store_false")
    r.set_defaults(memory=False)
    r.add_argument("--path", dest="path_or_id", default=None, help="Local fixture/dataset path or remote id.")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--dev-slice", type=float, default=None)
    r.add_argument("--k", type=int, default=5)
    r.add_argument(
        "--budget-usd",
        type=float,
        default=DEFAULT_BUDGET_USD,
        help=f"Hard USD cap for this run (default ${DEFAULT_BUDGET_USD:.0f}; <=0 means no cap).",
    )
    r.add_argument("--grader", default=None,
                   help="CODE grader: swebench (Docker), overlap (offline heuristic), or none.")
    r.add_argument("--grader-skip-unavailable", action="store_true",
                   help="With --grader swebench: leave tasks ungraded if Docker/swebench is absent (else error).")
    r.add_argument("--out", default=None, help="Optional per-task trajectory JSONL.")
    r.add_argument("--results", default=DEFAULT_PATH, help="Ledger path (default ./results.json).")
    r.add_argument("--run-id", default="")
    r.add_argument("--notes", default="")

    s = sub.add_parser("show", help="Print a compact summary of the ledger.")
    s.add_argument("--path", default=DEFAULT_PATH)

    sm = sub.add_parser("summary", help="Print the hypothesis scoreboard (Haiku+mem vs Opus no-mem).")
    sm.add_argument("--path", default=DEFAULT_PATH)
    sm.add_argument("--efficiency-budget", type=float, default=None,
                    help="Memory-overhead ceiling (default 0.10 = 10%).")
    sm.add_argument("--min-wins", type=int, default=None,
                    help="Benchmarks that must win for the criterion (default 2).")
    sm.add_argument("--json", action="store_true", help="Emit the summary as JSON.")

    args = p.parse_args(argv)

    if args.cmd == "summary":
        from . import aggregate
        kw: dict[str, Any] = {}
        if args.efficiency_budget is not None:
            kw["efficiency_budget"] = args.efficiency_budget
        if args.min_wins is not None:
            kw["min_wins"] = args.min_wins
        summary = aggregate.summarize(load_results(args.path), **kw)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(aggregate.format_summary(summary))
        return 0 if summary["criterion_met"] else 2

    if args.cmd == "show":
        data = load_results(args.path)
        runs = data.get("runs", [])
        print(f"{len(runs)} run(s) · updated {data.get('updated') or '—'} · {args.path}")
        for rec in runs:
            m = rec.get("metrics", {})
            print(
                f"  {rec.get('benchmark',''):<18} {rec.get('label',rec.get('model','')):<22} "
                f"acc={m.get('accuracy',0):.3f} rel={m.get('relevancy',0):.3f} "
                f"rec={m.get('recency',0):.3f} eff={m.get('efficiency',0):.3f} "
                f"n={rec.get('n_tasks',0)} ${rec.get('cost_usd',0):.4f}"
            )
        return 0

    # run + log
    from .cost import CostTracker
    from .harness import run
    from .models import get_model
    from .schema import Benchmark

    # default $10 cap; a value <= 0 disables the cap (pure accounting).
    cost = CostTracker(budget_usd=args.budget_usd) if args.budget_usd and args.budget_usd > 0 else None
    grader = None
    if args.grader:
        from . import grader as grader_mod
        gkwargs: dict[str, Any] = {}
        if args.grader.strip().lower() in ("swebench", "docker", "swebench-docker"):
            gkwargs["on_unavailable"] = "skip" if args.grader_skip_unavailable else "error"
        grader = grader_mod.get_grader(args.grader, **gkwargs)

    logger = None
    if args.out:
        from .trajectory import TrajectoryLogger
        logger = TrajectoryLogger(args.out, append=False)
    try:
        rr = run(
            Benchmark.from_str(args.benchmark),
            get_model(args.model),
            args.memory,
            limit=args.limit,
            dev_slice=args.dev_slice,
            path_or_id=args.path_or_id,
            cost=cost,
            logger=logger,
            grader=grader,
            k=args.k,
        )
    finally:
        if logger is not None:
            logger.close()

    rec = append_result(rr, args.results, run_id=args.run_id, notes=args.notes)
    m = rec["metrics"]
    print(
        f"logged {rec['benchmark']} · {rec['label']} -> {args.results}\n"
        f"  accuracy={m['accuracy']:.3f} relevancy={m['relevancy']:.3f} "
        f"recency={m['recency']:.3f} efficiency={m['efficiency']:.3f} "
        f"n={rec['n_tasks']} cost=${rec['cost_usd']:.4f}"
    )
    return 1 if rr.budget_exceeded else 0


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_PATH",
    "result_record",
    "load_results",
    "append_record",
    "append_result",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
