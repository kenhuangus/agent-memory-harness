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
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from memeval.stores.tests.scale_retrieval.helpers import (  # noqa: E402
    CONTROL,
    MatrixCell,
    Skip,
    close_cells,
    evaluate_case,
    iter_matrix_cells,
    load_cases,
    load_items,
    summarize_rows,
)


def run_matrix(
    *,
    quality_path: Path,
    cases_path: Path,
    filler_path: Path | None,
    tmp_root: Path,
    case_limit: int | None = None,
    live: bool = False,
) -> dict[str, Any]:
    quality = load_items(quality_path)
    filler = load_items(filler_path) if filler_path is not None and filler_path.exists() else []
    cases = load_cases(cases_path)
    if case_limit is not None:
        controls = [case for case in cases if case.kind == CONTROL]
        challenges = [case for case in cases if case.kind != CONTROL]
        cases = (controls + challenges)[:case_limit]
    cells = iter_matrix_cells(quality + filler, tmp_root, include_skips=True, live=live)
    report: dict[str, Any] = {
        "mode": "live" if live else "offline",
        "quality_count": len(quality),
        "filler_count": len(filler),
        "case_count": len(cases),
        "cells": {},
    }
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
                "summary": summarize_rows(rows),
                "by_lens_kind": {
                    key: summarize_rows(value) for key, value in sorted(groups.items())
                },
                "rows": rows,
            }
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
        f"Mode: `{report['mode']}`",
        f"Quality items: {report['quality_count']}",
        f"Filler items: {report['filler_count']}",
        f"Cases: {report['case_count']}",
        "",
        "cell | status | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 | p50 ns | p95 ns | reason",
        "--- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---",
    ]
    for name, cell in report["cells"].items():
        if cell["status"] == "skip":
            lines.append(f"{name} | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | {cell['reason']}")
            continue
        summary = cell["summary"]
        lines.append(
            f"{name} | ok | {_fmt(summary.get('recall@1'))} | {_fmt(summary.get('recall@5'))} | "
            f"{_fmt(summary.get('recall@10'))} | {_fmt(summary.get('MRR@10'))} | "
            f"{_fmt(summary.get('nDCG@10'))} | {_fmt(summary.get('latency_p50_ns'))} | "
            f"{_fmt(summary.get('latency_p95_ns'))} | "
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--filler", type=Path)
    parser.add_argument("--tmp-root", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    report = run_matrix(
        quality_path=args.quality,
        cases_path=args.cases,
        filler_path=args.filler,
        tmp_root=args.tmp_root,
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
