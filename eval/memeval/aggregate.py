"""Hypothesis scoreboard — owner: Ken. Aggregate the run ledger into a verdict.

The Results page lists every run; this module answers the question the project
actually poses: **does cheap Haiku + the memory harness close the gap to Opus 4.8
running without memory?** It reduces the raw ``results.json`` rows to a per-benchmark
comparison and a single pass/fail on the frozen success criterion:

    On >= ``min_wins`` of the five benchmarks, *Haiku + memory* beats the
    *Opus 4.8 no-memory* baseline on accuracy, **without** blowing the efficiency
    budget (memory-token overhead <= ``efficiency_budget``, default 10%).

Roles per benchmark (selected from the ledger by model id + memory flag, latest
row wins):

* **treatment** — a Haiku model with ``memory=True``.
* **baseline**  — an Opus 4.8 model with ``memory=False`` (the bar to beat).
* **lower_bound** — Haiku with ``memory=False`` (what memory adds), for context.
* **reference**  — Sonnet with ``memory=False``, for context.

A benchmark is a **win** when ``accuracy(treatment) >= accuracy(baseline)`` *and*
that accuracy is non-zero (a 0-vs-0 tie is not a win) *and* the treatment's memory
overhead is within ``efficiency_budget``. Stdlib-only; pure function of the ledger.
"""

from __future__ import annotations

from typing import Any, Optional

#: Memory-token overhead ceiling (treatment ``efficiency`` metric; lower is better).
DEFAULT_EFFICIENCY_BUDGET = 0.10
#: Benchmarks must win on at least this many to satisfy the success criterion.
DEFAULT_MIN_WINS = 2

#: Canonical benchmark order + display labels (mirrors the Results page).
BENCHMARKS: list[tuple[str, str]] = [
    ("memoryagentbench", "MemoryAgentBench"),
    ("longmemeval", "LongMemEval"),
    ("swe_contextbench", "SWE-ContextBench"),
    ("swe_bench_cl", "SWE-Bench-CL"),
    ("contextbench", "ContextBench"),
]


def _model_id(row: dict) -> str:
    return str(row.get("model", "")).lower()


def _is_haiku(row: dict) -> bool:
    return "haiku" in _model_id(row)


def _is_opus(row: dict) -> bool:
    return "opus" in _model_id(row)


def _is_sonnet(row: dict) -> bool:
    return "sonnet" in _model_id(row)


def _mem_on(row: dict) -> bool:
    return bool(row.get("memory"))


def _pick_latest(rows: list[dict], predicate) -> Optional[dict]:
    """Most-recent ledger row matching ``predicate`` (by ISO ``timestamp``)."""
    matches = [r for r in rows if predicate(r)]
    if not matches:
        return None
    return max(matches, key=lambda r: str(r.get("timestamp", "")))


def _acc(row: Optional[dict]) -> Optional[float]:
    if not row:
        return None
    v = (row.get("metrics") or {}).get("accuracy")
    return None if v is None else float(v)


def _eff(row: Optional[dict]) -> Optional[float]:
    if not row:
        return None
    v = (row.get("metrics") or {}).get("efficiency")
    return None if v is None else float(v)


def _role_view(row: Optional[dict]) -> Optional[dict]:
    """Compact, JSON-friendly view of a selected row (or ``None``)."""
    if not row:
        return None
    m = row.get("metrics") or {}
    return {
        "label": row.get("label") or row.get("model"),
        "model": row.get("model"),
        "memory": bool(row.get("memory")),
        "accuracy": _acc(row),
        "efficiency": _eff(row),
        "n_tasks": row.get("n_tasks"),
        "partial": bool(row.get("partial")),
        "cost_usd": row.get("cost_usd"),
        "relevancy": m.get("relevancy"),
        "recency": m.get("recency"),
    }


def summarize_benchmark(
    rows: list[dict],
    *,
    efficiency_budget: float = DEFAULT_EFFICIENCY_BUDGET,
) -> dict:
    """Reduce one benchmark's rows to a treatment-vs-baseline comparison."""
    treatment = _pick_latest(rows, lambda r: _is_haiku(r) and _mem_on(r))
    baseline = _pick_latest(rows, lambda r: _is_opus(r) and not _mem_on(r))
    lower_bound = _pick_latest(rows, lambda r: _is_haiku(r) and not _mem_on(r))
    reference = _pick_latest(rows, lambda r: _is_sonnet(r) and not _mem_on(r))

    acc_t, acc_b = _acc(treatment), _acc(baseline)
    eff_t = _eff(treatment)
    comparable = acc_t is not None and acc_b is not None

    acc_delta = (acc_t - acc_b) if comparable else None
    acc_win = bool(comparable and acc_t >= acc_b and acc_t > 0.0)
    eff_ok = bool(eff_t is not None and eff_t <= efficiency_budget)
    win = bool(acc_win and eff_ok)

    if not comparable:
        status = "incomplete"  # need both a treatment and a baseline row
    elif win:
        status = "win"
    elif acc_win and not eff_ok:
        status = "over_budget"  # accuracy clears the bar, overhead does not
    else:
        status = "loss"

    return {
        "treatment": _role_view(treatment),
        "baseline": _role_view(baseline),
        "lower_bound": _role_view(lower_bound),
        "reference": _role_view(reference),
        "accuracy_treatment": acc_t,
        "accuracy_baseline": acc_b,
        "accuracy_delta": acc_delta,
        "efficiency_treatment": eff_t,
        "efficiency_budget": efficiency_budget,
        "comparable": comparable,
        "acc_win": acc_win,
        "eff_ok": eff_ok,
        "win": win,
        "status": status,
    }


def summarize(
    data: dict,
    *,
    efficiency_budget: float = DEFAULT_EFFICIENCY_BUDGET,
    min_wins: int = DEFAULT_MIN_WINS,
) -> dict:
    """Aggregate a loaded ledger (``{"runs": [...]}``) into the hypothesis verdict.

    Returns a JSON-serializable summary: one entry per benchmark that has any
    rows, the win count, and whether the success criterion is met. ``data`` is
    the object returned by :func:`memeval.results.load_results`.
    """
    runs = data.get("runs", []) if isinstance(data, dict) else []
    by_bench: dict[str, list[dict]] = {}
    for r in runs:
        by_bench.setdefault(str(r.get("benchmark", "")), []).append(r)

    benchmarks: list[dict] = []
    # Canonical order first, then any unknown benchmarks present in the ledger.
    keys = [k for k, _ in BENCHMARKS] + [k for k in by_bench if k not in dict(BENCHMARKS)]
    labels = dict(BENCHMARKS)
    for key in keys:
        rows = by_bench.get(key)
        if not rows:
            continue
        entry = {"benchmark": key, "label": labels.get(key, key)}
        entry.update(summarize_benchmark(rows, efficiency_budget=efficiency_budget))
        benchmarks.append(entry)

    wins = sum(1 for b in benchmarks if b["win"])
    comparable = sum(1 for b in benchmarks if b["comparable"])
    criterion_met = wins >= min_wins

    return {
        "efficiency_budget": efficiency_budget,
        "min_wins": min_wins,
        "n_benchmarks": len(benchmarks),
        "n_comparable": comparable,
        "wins": wins,
        "criterion_met": criterion_met,
        "benchmarks": benchmarks,
    }


def format_summary(summary: dict) -> str:
    """Human-readable scoreboard for the CLI."""
    lines: list[str] = []
    budget = summary["efficiency_budget"]
    lines.append(
        f"Hypothesis: Haiku + memory beats Opus-4.8 no-memory on "
        f">= {summary['min_wins']} / 5 benchmarks (overhead <= {budget:.0%})."
    )
    lines.append("")
    header = f"  {'Benchmark':18} {'Haiku+mem':>9} {'Opus':>6} {'dAcc':>7} {'overhd':>7}  verdict"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    sym = {"win": "WIN", "over_budget": "over-budget", "loss": "-", "incomplete": "n/a"}
    for b in summary["benchmarks"]:
        at = b["accuracy_treatment"]
        ab = b["accuracy_baseline"]
        da = b["accuracy_delta"]
        eff = b["efficiency_treatment"]
        lines.append(
            f"  {b['label']:18} "
            f"{('-' if at is None else f'{at:.2f}'):>9} "
            f"{('-' if ab is None else f'{ab:.2f}'):>6} "
            f"{('-' if da is None else f'{da:+.2f}'):>7} "
            f"{('-' if eff is None else f'{eff:.0%}'):>7}  "
            f"{sym.get(b['status'], b['status'])}"
        )
    lines.append("")
    verdict = "MET" if summary["criterion_met"] else "NOT MET"
    lines.append(
        f"  Wins: {summary['wins']} / {summary['n_benchmarks']} "
        f"(need {summary['min_wins']}) -> success criterion {verdict}"
    )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_EFFICIENCY_BUDGET",
    "DEFAULT_MIN_WINS",
    "BENCHMARKS",
    "summarize",
    "summarize_benchmark",
    "format_summary",
]
