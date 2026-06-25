#!/usr/bin/env python3
"""Run the scale-retrieval matrix and write JSON/Markdown reports.

Offline use is CI-safe and skips Voyage/native-future cells. Captained live use is
explicit and double-gated:

    MEMEVAL_LIVE=1 VOYAGE_API_KEY=... python3 eval/memeval/stores/tests/scale_retrieval/tools/report_scale_retrieval.py --live ...
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[6]
EVAL_DIR = ROOT / "eval"
SCALE_RETRIEVAL_DIR = Path(__file__).resolve().parents[1]
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from memeval.stores.tests.scale_retrieval.helpers import (  # noqa: E402
    CONTROL,
    CHALLENGE,
    CURRENT_CELL_NAMES,
    LENSES,
    MatrixCell,
    Skip,
    _current_cell,
    close_cells,
    evaluate_case,
    fts5_cells,
    iter_matrix_cells,
    load_cases,
    load_items,
    local_ann_cells,
    summarize_rows,
    skip_cells,
)


def _retained_counts(cases: list[Any]) -> dict[str, int]:
    return {lens: sum(1 for case in cases if case.lens == lens) for lens in LENSES}


def _lift_table(cases: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for case in cases:
        if case.kind == CHALLENGE:
            grouped[case.lens].append(case)
    rows: list[dict[str, Any]] = []
    for lens, lens_cases in sorted(grouped.items()):
        floor_recall = []
        floor_mrr = []
        target_recall = []
        target_mrr = []
        for case in lens_cases:
            calibration = case.calibration or {}
            floor_metrics = calibration.get("floor_metrics") or {}
            target_metrics = calibration.get("target_metrics") or {}
            if not floor_metrics or not target_metrics:
                continue
            floor_recall.append(float(floor_metrics.get("recall@10", 0.0)))
            floor_mrr.append(float(floor_metrics.get("MRR@10", 0.0)))
            target_recall.append(float(target_metrics.get("recall@10", 0.0)))
            target_mrr.append(float(target_metrics.get("MRR@10", 0.0)))
        if not target_recall:
            continue
        n = len(target_recall)
        rows.append({
            "lens": lens,
            "n": n,
            "floor_recall@10": sum(floor_recall) / n,
            "floor_MRR@10": sum(floor_mrr) / n,
            "target_recall@10": sum(target_recall) / n,
            "target_MRR@10": sum(target_mrr) / n,
            "recall_lift": (sum(target_recall) - sum(floor_recall)) / n,
            "MRR_lift": (sum(target_mrr) - sum(floor_mrr)) / n,
        })
    return rows


def _caption(*, report: dict[str, Any], manifest: dict[str, Any] | None) -> str:
    counts = report["retained_counts_by_lens"]
    retained = ", ".join(f"{lens}={counts.get(lens, 0)}" for lens in LENSES)
    candidate = manifest["candidate_count"] if manifest else "n/a"
    return (
        "Offline deterministic matrix baseline using the stdlib hashing embedder; "
        f"quality={report['quality_count']}, filler={report['filler_count']}, "
        f"candidate={candidate}, retained={report['case_count']} ({retained}). "
        "Semantic accuracy and the route-vs-fuse accuracy verdict require the captained "
        "Voyage run; this artifact is the deterministic mechanism, lexical/relational, "
        "and latency baseline."
    )


def run_matrix(
    *,
    quality_path: Path,
    cases_path: Path,
    filler_path: Path | None,
    tmp_root: Path,
    manifest_path: Path | None = None,
    case_limit: int | None = None,
    live: bool = False,
) -> dict[str, Any]:
    quality = load_items(quality_path)
    filler = load_items(filler_path) if filler_path is not None and filler_path.exists() else []
    cases = load_cases(cases_path)
    manifest = json.loads(manifest_path.read_text()) if manifest_path is not None and manifest_path.exists() else None
    if case_limit is not None:
        controls = [case for case in cases if case.kind == CONTROL]
        challenges = [case for case in cases if case.kind != CONTROL]
        cases = (controls + challenges)[:case_limit]
    report: dict[str, Any] = {
        "mode": "live" if live else "offline",
        "quality_count": len(quality),
        "filler_count": len(filler),
        "case_count": len(cases),
        "retained_counts_by_lens": _retained_counts(cases),
        "calibration_lift_by_lens": _lift_table(cases),
        "cells": {},
    }
    report["caption"] = _caption(report=report, manifest=manifest)
    def record_cell(cell: MatrixCell) -> None:
        rows: list[dict[str, Any]] = []
        for case in cases:
            result = evaluate_case(cell.store, case, k=10)
            rows.append({
                "case_id": case.case_id,
                "lens": case.lens,
                "kind": case.kind,
                **result,
            })
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[f"{row['lens']}:{row['kind']}"].append(row)
        report["cells"][cell.name] = {
            "status": "ok",
            "columns": cell.columns,
            "write_summary": cell.write_summary,
            "summary": summarize_rows(rows),
            "by_lens_kind": {
                key: summarize_rows(value) for key, value in sorted(groups.items())
            },
            "rows": rows,
        }

    if not live:
        seen: set[str] = set()
        for name in CURRENT_CELL_NAMES:
            cell = _current_cell(name, quality + filler, tmp_root)
            try:
                record_cell(cell)
                seen.add(cell.name)
            finally:
                cell.close()
        for local_cell in local_ann_cells(quality + filler, tmp_root, include_skips=True):
            if isinstance(local_cell, Skip):
                report["cells"][local_cell.name] = {
                    "status": "skip",
                    "reason": local_cell.reason,
                    "columns": local_cell.columns,
                }
                seen.add(local_cell.name)
                continue
            try:
                record_cell(local_cell)
                seen.add(local_cell.name)
            finally:
                local_cell.close()
        for fts5_cell in fts5_cells(quality + filler, tmp_root, include_skips=True):
            if isinstance(fts5_cell, Skip):
                report["cells"][fts5_cell.name] = {
                    "status": "skip",
                    "reason": fts5_cell.reason,
                    "columns": fts5_cell.columns,
                }
                seen.add(fts5_cell.name)
                continue
            try:
                record_cell(fts5_cell)
                seen.add(fts5_cell.name)
            finally:
                fts5_cell.close()
        for skip in skip_cells():
            if skip.name in seen:
                continue
            report["cells"][skip.name] = {
                "status": "skip",
                "reason": skip.reason,
                "columns": skip.columns,
            }
        return report

    cells = iter_matrix_cells(quality + filler, tmp_root, include_skips=True, live=live)
    try:
        for cell in cells:
            if isinstance(cell, Skip):
                report["cells"][cell.name] = {
                    "status": "skip",
                    "reason": cell.reason,
                    "columns": cell.columns,
                }
                continue
            assert isinstance(cell, MatrixCell)
            record_cell(cell)
    finally:
        close_cells(cells)
    return report


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Scale Retrieval Matrix",
        "",
        f"Caption: {report['caption']}",
        "",
        f"Mode: `{report['mode']}`",
        f"Quality items: {report['quality_count']}",
        f"Filler items: {report['filler_count']}",
        f"Cases: {report['case_count']}",
        "Retained counts by lens: " + ", ".join(
            f"{lens}={report['retained_counts_by_lens'].get(lens, 0)}" for lens in LENSES
        ),
        "",
        "## Matrix",
        "",
        "cell | status | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 | write p50 ns | write p95 ns | write/s | search p50 ns | search p95 ns | search/s | reason",
        "--- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---",
    ]
    for name, cell in report["cells"].items():
        if cell["status"] == "skip":
            lines.append(f"{name} | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | {cell['reason']}")
            continue
        summary = cell["summary"]
        write = cell["write_summary"]
        lines.append(
            f"{name} | ok | {_fmt(summary.get('recall@1'))} | {_fmt(summary.get('recall@5'))} | "
            f"{_fmt(summary.get('recall@10'))} | {_fmt(summary.get('MRR@10'))} | "
            f"{_fmt(summary.get('nDCG@10'))} | {_fmt(write.get('latency_p50_ns'))} | "
            f"{_fmt(write.get('latency_p95_ns'))} | {_fmt(write.get('throughput_per_s'))} | "
            f"{_fmt(summary.get('latency_p50_ns'))} | {_fmt(summary.get('latency_p95_ns'))} | "
            f"{_fmt(summary.get('throughput_per_s'))} | "
        )
    lines.extend([
        "",
        "## Calibration Lift",
        "",
        "lens | n | floor recall@10 | floor MRR@10 | target recall@10 | target MRR@10 | recall lift | MRR lift",
        "--- | ---: | ---: | ---: | ---: | ---: | ---: | ---:",
    ])
    for row in report["calibration_lift_by_lens"]:
        lines.append(
            f"{row['lens']} | {row['n']} | {_fmt(row['floor_recall@10'])} | "
            f"{_fmt(row['floor_MRR@10'])} | {_fmt(row['target_recall@10'])} | "
            f"{_fmt(row['target_MRR@10'])} | {_fmt(row['recall_lift'])} | {_fmt(row['MRR_lift'])}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", type=Path, default=SCALE_RETRIEVAL_DIR / "quality_items.jsonl")
    parser.add_argument("--cases", type=Path, default=SCALE_RETRIEVAL_DIR / "cases.retained.jsonl")
    parser.add_argument("--filler", type=Path, default=SCALE_RETRIEVAL_DIR / "filler_items.jsonl")
    parser.add_argument("--tmp-root", type=Path, default=Path("/tmp/memeval_scale_retrieval_matrix"))
    parser.add_argument("--out-json", type=Path, default=SCALE_RETRIEVAL_DIR / "results" / "offline_matrix.json")
    parser.add_argument("--out-md", type=Path, default=SCALE_RETRIEVAL_DIR / "results" / "offline_matrix.md")
    parser.add_argument("--manifest", type=Path, default=SCALE_RETRIEVAL_DIR / "calibration_manifest.offline.json")
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    report = run_matrix(
        quality_path=args.quality,
        cases_path=args.cases,
        filler_path=args.filler,
        tmp_root=args.tmp_root,
        manifest_path=args.manifest,
        case_limit=args.case_limit,
        live=args.live,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(report, args.out_md)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")


if __name__ == "__main__":
    main()
