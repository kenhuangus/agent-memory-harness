"""Results + cross-stage summary for the SWE-Bench-CL pipeline (ADR-eval-003/004).

The pipeline reuses the standard result machinery (``RunResult`` -> ``result_record``)
and writes ONE per-benchmark file holding every stage's row, plus two blocks the
ordinary ledger does not carry:

* a top-level ``pipeline`` metadata block -- the run's provenance the shared memory
  substrate can no longer record (which sequence, model, dreamer model, version, ...);
* a ``dream`` block -- the summary the plugin's ``daydream-cli dream`` emitted.

A derived ``SUMMARY-<bench>-<stamp>.md`` (+ ``.json``) tabulates the stages and the
base->final deltas for human comparison. Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..results import (
    SCHEMA_VERSION,
    benchmark_results_path,
    normalize_version,
    result_record,
)

#: Metric keys surfaced in the summary table (in display order). ``efficiency`` is
#: lower-is-better; the rest are higher-is-better.
_SUMMARY_METRICS = ("accuracy", "relevancy", "recency", "efficiency")


def stage_row(rr: Any, *, stage: str, stage_index: int, pipeline_meta: dict) -> dict:
    """Flatten a stage's ``RunResult`` into a ledger row, stamped with stage identity
    and git provenance via the existing ``extra=`` channel."""
    return result_record(
        rr,
        run_id=f"pipeline-{stage}",
        notes=f"5-stage SWE-Bench-CL pipeline · stage {stage_index} ({stage})",
        extra={
            "pipeline_stage": stage,
            "stage_index": stage_index,
            "git_sha": pipeline_meta.get("git_sha", ""),
            "git_tag": pipeline_meta.get("version", ""),
        },
    )


def write_pipeline_results(
    *,
    benchmark: str,
    version: str,
    timestamp: str,
    rows: list[dict],
    pipeline_meta: dict,
    dream: Optional[dict],
    root: "str | Path" = "results",
) -> Path:
    """Write the per-benchmark pipeline result file and return its path.

    Self-describing: schema, memory version, benchmark, timestamp, the per-stage run
    rows, the ``pipeline`` provenance block, and the ``dream`` block. Reuses
    :func:`benchmark_results_path` so it lands beside ordinary results under
    ``{root}/v{version}/``."""
    path = benchmark_results_path(benchmark, version=version, timestamp=timestamp, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": SCHEMA_VERSION,
        "memory_version": normalize_version(version),
        "benchmark": benchmark,
        "timestamp": timestamp,
        "pipeline": pipeline_meta,
        "dream": dream or {"status": "not-run"},
        "runs": list(rows),
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Cross-stage SUMMARY (markdown + json)
# --------------------------------------------------------------------------- #
def _metrics_of(row: dict) -> dict:
    return dict(row.get("metrics") or {})


def _delta(a: dict, b: dict, key: str) -> Optional[float]:
    av, bv = a.get(key), b.get(key)
    if av is None or bv is None:
        return None
    return round(bv - av, 4)


def build_summary(
    *,
    benchmark: str,
    rows: list[dict],
    pipeline_meta: dict,
    dream: Optional[dict],
    native_by_stage: Optional[dict[str, dict]] = None,
) -> dict:
    """Build the machine-readable summary: per-stage metrics, native CL headline per
    stage, and the base->final + step deltas. The ``rows`` are stage rows in stage
    order; ``native_by_stage`` maps a stage name to its native CL report dict."""
    by_stage = {r.get("pipeline_stage"): r for r in rows}
    native_by_stage = native_by_stage or {}

    stages_out = []
    for r in rows:
        stage = r.get("pipeline_stage")
        entry = {
            "stage": stage,
            "stage_index": r.get("stage_index"),
            "metrics": {k: _metrics_of(r).get(k) for k in _SUMMARY_METRICS},
            "n_tasks": r.get("n_tasks"),
            "cost_usd": r.get("cost_usd"),
            "memory_reached": (r.get("reliability") or {}).get("memory_reached"),
        }
        if stage in native_by_stage:
            entry["native_cl"] = _native_headline(native_by_stage[stage])
        stages_out.append(entry)

    base = _metrics_of(by_stage.get("base", {}))
    blank = _metrics_of(by_stage.get("plugin-blank", {}))
    accum = _metrics_of(by_stage.get("plugin-accum", {}))
    final = _metrics_of(by_stage.get("plugin-dreamed", {}))

    deltas = {
        "base_to_blank": {k: _delta(base, blank, k) for k in _SUMMARY_METRICS},
        "blank_to_accum": {k: _delta(blank, accum, k) for k in _SUMMARY_METRICS},
        "accum_to_dreamed": {k: _delta(accum, final, k) for k in _SUMMARY_METRICS},
        "base_to_final": {k: _delta(base, final, k) for k in _SUMMARY_METRICS},
    }

    return {
        "benchmark": benchmark,
        "pipeline": pipeline_meta,
        "dream": dream or {"status": "not-run"},
        "stages": stages_out,
        "deltas": deltas,
    }


def _native_headline(report: dict) -> dict:
    """Pull the headline CL metrics (ACC/F/BWT/FWT/AULC/CL-Score) out of a native report
    dict's flat ``metrics`` list, keyed by name."""
    out: dict[str, float] = {}
    for m in report.get("metrics", []):
        name = m.get("name")
        if name:
            out[name] = m.get("value")
    return out


def _fmt(v: Any, *, signed: bool = False) -> str:
    """Format a metric cell. ``signed`` shows an explicit +/- (for deltas)."""
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:+.4f}" if signed else f"{v:.4f}"
    return str(v)


def render_summary_md(summary: dict) -> str:
    """Render the human-readable SUMMARY markdown from :func:`build_summary` output."""
    pm = summary.get("pipeline", {})
    lines: list[str] = []
    lines.append(f"# Pipeline summary — {summary.get('benchmark')}")
    lines.append("")
    lines.append(
        f"**Version:** {pm.get('version')} · **Sequence:** {pm.get('sequence')} · "
        f"**Model:** {pm.get('model')} · **Tasks:** {pm.get('n_tasks')} · "
        f"**Stages:** {pm.get('n_stages')}"
    )
    dreamer = pm.get("dream") or {}
    lines.append(
        f"**Dreamer:** {dreamer.get('provider')} / {dreamer.get('model')} · "
        f"**Grader:** {pm.get('grader')} · **git:** {pm.get('git_sha')}"
    )
    lines.append("")

    # Per-stage metric table.
    header = "| Stage | " + " | ".join(_SUMMARY_METRICS) + " | n | cost |"
    sep = "|" + "---|" * (len(_SUMMARY_METRICS) + 3)
    lines.append(header)
    lines.append(sep)
    for s in summary.get("stages", []):
        m = s.get("metrics", {})
        cells = " | ".join(_fmt(m.get(k)) for k in _SUMMARY_METRICS)
        cost = s.get("cost_usd")
        cost_cell = f"${cost:.4f}" if isinstance(cost, (int, float)) else "—"
        lines.append(f"| {s.get('stage')} | {cells} | {s.get('n_tasks')} | {cost_cell} |")
    lines.append("")

    # Deltas (base -> final the headline).
    lines.append("## Deltas")
    lines.append("")
    lines.append("| Transition | " + " | ".join(_SUMMARY_METRICS) + " |")
    lines.append("|" + "---|" * (len(_SUMMARY_METRICS) + 1))
    for label, d in summary.get("deltas", {}).items():
        cells = " | ".join(_fmt(d.get(k), signed=True) for k in _SUMMARY_METRICS)
        lines.append(f"| {label} | {cells} |")
    lines.append("")

    # Native CL headline per stage (if captured).
    native_stages = [s for s in summary.get("stages", []) if s.get("native_cl")]
    if native_stages:
        lines.append("## Native continual-learning metrics")
        lines.append("")
        keys = sorted({k for s in native_stages for k in s["native_cl"]})
        lines.append("| Stage | " + " | ".join(keys) + " |")
        lines.append("|" + "---|" * (len(keys) + 1))
        for s in native_stages:
            cells = " | ".join(_fmt(s["native_cl"].get(k)) for k in keys)
            lines.append(f"| {s.get('stage')} | {cells} |")
        lines.append("")

    # Dream block.
    dream = summary.get("dream") or {}
    status = dream.get("status")
    lines.append("## Dream consolidation")
    lines.append("")
    if status == "not-run":
        lines.append("_not run_")
    elif status == "not-implemented":
        lines.append(f"_not implemented — no-op_ ({dream.get('note', '')})")
    elif status in ("skipped", "error"):
        reason = dream.get("reason") or dream.get("error_type") or status
        lines.append(f"_{status}: {reason}_")
    else:
        counts = dream.get("counts") or {}
        if dream.get("jobs_run") is not None or dream.get("skipped_jobs") is not None:
            lines.append(
                f"- jobs: {dream.get('jobs_run')} · skipped: {dream.get('skipped_jobs')}"
            )
        if counts:
            lines.append(
                f"- items: {counts.get('total_items')} · duplicate clusters: "
                f"{counts.get('duplicate_clusters')} · items in duplicates: "
                f"{counts.get('items_in_duplicates')}"
            )
        note = dream.get("dream_consolidation") or dream.get("note") or dream.get("mode") or ""
        if note:
            lines.append(f"- note: {note}")
    lines.append("")
    return "\n".join(lines)


def write_summary(
    *,
    benchmark: str,
    version: str,
    timestamp: str,
    summary: dict,
    root: "str | Path" = "results",
) -> tuple[Path, Path]:
    """Write ``SUMMARY-<bench>-<stamp>.md`` and ``.json`` under ``{root}/v{version}/``.
    Returns ``(md_path, json_path)``."""
    base = Path(root) / normalize_version(version)
    base.mkdir(parents=True, exist_ok=True)
    md_path = base / f"SUMMARY-{benchmark}-{timestamp}.md"
    json_path = base / f"SUMMARY-{benchmark}-{timestamp}.json"
    md_path.write_text(render_summary_md(summary), encoding="utf-8")
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


__all__ = [
    "stage_row",
    "write_pipeline_results",
    "build_summary",
    "render_summary_md",
    "write_summary",
]
