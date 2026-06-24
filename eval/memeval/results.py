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


def native_metrics_block(report: Any) -> dict:
    """Flatten a :class:`~memeval.native.spec.BenchmarkNativeReport` (or its
    ``to_dict()``) into a ledger-friendly ``native`` block for the Results page.

    Returns ``{"benchmark", "mode", "n_tasks", "metrics": {name: {value, n,
    better, metadata}}, "components": {...}}`` — the headline native metrics
    keyed by name so the dashboard can read ``row.native.metrics.forgetting`` /
    ``row.native.metrics.poisoning_resistance`` directly. Accepts either a
    report object (has ``to_dict``) or an already-serialized dict; ``None`` /
    unrecognized input yields ``{}`` so callers degrade gracefully.

    This is reporting/results plumbing only — it surfaces the metrics the native
    evaluators already compute into the ledger the static page consumes, without
    touching any team-owned (dreaming/stores/plugin) code.
    """
    if report is None:
        return {}
    d = report.to_dict() if hasattr(report, "to_dict") else report
    if not isinstance(d, dict):
        return {}
    metrics_list = d.get("metrics") or []
    metrics: dict[str, Any] = {}
    for m in metrics_list:
        if isinstance(m, dict) and "name" in m:
            metrics[m["name"]] = {
                "value": m.get("value"),
                "n": m.get("n"),
                "better": m.get("better", "higher"),
                "metadata": m.get("metadata", {}),
            }
    return {
        "benchmark": d.get("benchmark"),
        "mode": d.get("mode"),
        "n_tasks": d.get("n_tasks"),
        "metrics": metrics,
        "components": d.get("components", {}),
    }


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
        # Dataset accounting (reported for every run): how many dataset entries
        # were actually evaluated, how many the dataset held, and the --limit
        # that was applied (None = no cap). entries_used mirrors n_tasks.
        "entries_used": rr.n_tasks,
        "entries_available": rr.metadata.get("total_available"),
        "limit": rr.metadata.get("limit"),
        "selection": rr.metadata.get("select", "flat"),
        "cost_usd": round(rr.cost_usd, 6),
        "tokens_in": rr.tokens_in,
        "tokens_out": rr.tokens_out,
        "partial": rr.partial,
        "budget_exceeded": rr.budget_exceeded,
        "source": rr.metadata.get("source", ""),
        # Reliability/robustness documentation. The memory layer is a WIP, so a run
        # is expected to recover from per-task errors and report them here rather
        # than abort. n_errors/errors = tasks that failed (with reason); for memory
        # runs, memory_reached = tasks that actually retrieved (proof the memory
        # path worked), so memory_reached < n_tasks flags silent memory misses.
        "reliability": {
            "n_errors": rr.metadata.get("n_errors", 0),
            "memory_reached": rr.metadata.get("memory_reached", 0),
            "memory_hit": rr.metadata.get("memory_hit", 0),
            "recall_attempted": rr.metadata.get("recall_attempted", 0),
            "recall_with_hits": rr.metadata.get("recall_with_hits", 0),
            "graded_n": rr.metadata.get("graded_n", 0),
            # Task-success breakdown: resolved = passed, ungraded = could-not-grade,
            # grade_reasons = histogram of why (checkout_failed / env_build_failed /
            # graded / ...). Surfaces which tasks succeeded and why others didn't.
            "resolved": rr.metadata.get("resolved", 0),
            "ungraded": rr.metadata.get("ungraded", 0),
            "grade_reasons": rr.metadata.get("grade_reasons", {}),
            "errors": rr.metadata.get("errors", []),
        },
        "notes": notes,
    }
    # Surface a benchmark's NATIVE metrics (continual-learning suite for
    # swe_bench_cl; poisoning/calibration/RSI for vista) into the ledger row so
    # the Results page can render per-benchmark native panels. The native report
    # is passed through ``rr.metadata['native_report']`` (a BenchmarkNativeReport
    # or its dict); absent -> no ``native`` key (page shows n/a). Reporting-only.
    native = native_metrics_block(rr.metadata.get("native_report"))
    if native:
        rec["native"] = native
    if extra:
        rec.update(extra)
    return rec


def normalize_version(version: str) -> str:
    """Normalize a memory-system version to the ``vX.Y`` directory form.

    Accepts ``"0.1"``, ``"v0.1"``, ``"V0.1"`` -> ``"v0.1"``. Leaves any other
    shape alone beyond ensuring a single leading ``v`` (so ``"0.2.1"`` -> ``"v0.2.1"``).
    """
    v = str(version).strip().lstrip("vV")
    return f"v{v}"


def run_timestamp() -> str:
    """Filesystem-safe UTC timestamp for result filenames, e.g. ``20260620T193045Z``."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_pipeline_version(
    *, cwd: "str | Path | None" = None, override: "str | None" = None,
) -> dict[str, Any]:
    """Resolve a pipeline run's version from the git tag on its commit (ADR-eval-004).

    A pipeline keys both its results directory and its persistent memory substrate on
    this version, so a new release starts from a fresh substrate rather than mixing two
    code generations' memory. Resolution order:

    1. Explicit ``override`` -> use that reusable name as-is after normalizing it.
    2. ``git describe --tags --exact-match HEAD`` -- HEAD is exactly a tag -> use it.
    3. ``git describe --tags --abbrev=0`` -- nearest reachable tag -> use it, and flag
       that HEAD is *past* the tag (``version_exact=False``).
    4. The current **branch name + commit SHA** (when on an untagged commit) -> use
       a sanitized, filesystem-safe branch form plus the short SHA so local runs on
       different commits get fresh memory by default. Flagged ``untagged=True``.
    5. :data:`memeval.MEMORY_VERSION` -- detached HEAD / no branch / no git -> final fallback.

    Returns a dict ``{version, version_exact, untagged, git_sha, branch, source}`` where
    ``version`` is normalized to the ``vX.Y``-style directory form (:func:`normalize_version`)
    and ``source`` is ``"exact-tag" | "nearest-tag" | "override" | "branch-commit" |
    "memory-version"``. Never raises -- a missing git checkout degrades to the
    ``MEMORY_VERSION`` fallback, since the resolver is metadata, not metric logic.
    """
    import subprocess

    from . import MEMORY_VERSION

    def _git(*args: str) -> "str | None":
        try:
            out = subprocess.run(
                ["git", *args], cwd=str(cwd) if cwd else None,
                capture_output=True, text=True, timeout=10, check=False,
            )
        except Exception:
            return None
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None

    git_sha = _git("rev-parse", "--short", "HEAD") or ""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")  # "HEAD" when detached

    if override:
        return {"version": normalize_version(override), "version_exact": False,
                "untagged": True, "git_sha": git_sha, "branch": branch, "source": "override"}

    exact = _git("describe", "--tags", "--exact-match", "HEAD")
    if exact:
        return {"version": normalize_version(exact), "version_exact": True,
                "untagged": False, "git_sha": git_sha, "branch": branch, "source": "exact-tag"}

    nearest = _git("describe", "--tags", "--abbrev=0")
    if nearest:
        return {"version": normalize_version(nearest), "version_exact": False,
                "untagged": False, "git_sha": git_sha, "branch": branch, "source": "nearest-tag"}

    # Untagged commit: key the substrate by branch + commit so every untagged code
    # generation gets fresh memory by default. Use --results-version when deliberate
    # reuse across commits is desired.
    if branch and branch != "HEAD":
        return {"version": _branch_version(branch, git_sha), "version_exact": False,
                "untagged": True, "git_sha": git_sha, "branch": branch,
                "source": "branch-commit"}

    return {"version": normalize_version(MEMORY_VERSION), "version_exact": False,
            "untagged": True, "git_sha": git_sha, "branch": branch, "source": "memory-version"}


def _branch_version(branch: str, git_sha: str) -> str:
    """A filesystem-safe directory token for a branch-keyed substrate, e.g.
    ``eval/swe-bench-cl-pipeline`` at ``1a2b3c4`` ->
    ``vbranch-eval-swe-bench-cl-pipeline-1a2b3c4``.

    Slashes and any non ``[A-Za-z0-9._-]`` char become ``-`` (so the version never
    creates nested dirs), collapsed runs of ``-`` are squeezed, and a ``branch-`` prefix
    plus the ``v`` from :func:`normalize_version` keep it distinct from a real tag
    bucket. The short commit SHA is appended so repeated runs on the same branch get
    fresh memory when the code changes. Capped at a sane length."""
    import re

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-")
    safe = re.sub(r"-{2,}", "-", safe)[:48] or "unknown"
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", git_sha).strip("-") or "unknown"
    return normalize_version(f"branch-{safe}-{suffix}")


def benchmark_results_path(
    benchmark: str, *, version: str, timestamp: str, root: "str | Path" = "results",
) -> Path:
    """Path for one benchmark's result file: ``{root}/v{X.Y}/{bench}-{timestamp}.json``."""
    return Path(root) / normalize_version(version) / f"{benchmark}-{timestamp}.json"


def write_benchmark_results(
    benchmark: str, records: list[dict], *, version: str, timestamp: str,
    root: "str | Path" = "results",
) -> Path:
    """Write one benchmark's runs to ``{root}/v{X.Y}/{bench}-{timestamp}.json``.

    ``records`` are :func:`result_record` rows (e.g. the builtin + plugin runs for
    this benchmark). The file is self-describing: schema, memory version, the
    benchmark, the run timestamp, and the list of run records. Returns the path.
    """
    path = benchmark_results_path(benchmark, version=version, timestamp=timestamp, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": SCHEMA_VERSION,
        "memory_version": normalize_version(version),
        "benchmark": benchmark,
        "timestamp": timestamp,
        "runs": list(records),
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


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
                   help="CODE grader: local (host test execution, best-effort), "
                        "overlap (offline heuristic), or none.")
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
            used = rec.get("entries_used", rec.get("n_tasks", 0))
            avail = rec.get("entries_available")
            lim = rec.get("limit")
            entries = f"{used}/{avail}" if avail is not None else str(used)
            entries += f" (limit={lim})" if lim is not None else ""
            print(
                f"  {rec.get('benchmark',''):<18} {rec.get('label',rec.get('model','')):<22} "
                f"acc={m.get('accuracy',0):.3f} rel={m.get('relevancy',0):.3f} "
                f"rec={m.get('recency',0):.3f} eff={m.get('efficiency',0):.3f} "
                f"entries={entries} ${rec.get('cost_usd',0):.4f}"
            )
        return 0

    # run + log
    from .cost import CostTracker
    from .harness import run
    from .models import get_model
    from .schema import Benchmark

    # default cap (cost.DEFAULT_BUDGET_USD); a value <= 0 disables it (pure accounting).
    cost = CostTracker(budget_usd=args.budget_usd) if args.budget_usd and args.budget_usd > 0 else None
    grader = None
    if args.grader:
        from . import grader as grader_mod
        grader = grader_mod.get_grader(args.grader)

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
    "normalize_version",
    "run_timestamp",
    "resolve_pipeline_version",
    "benchmark_results_path",
    "write_benchmark_results",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
