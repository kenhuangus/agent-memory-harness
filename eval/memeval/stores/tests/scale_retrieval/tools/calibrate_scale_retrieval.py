#!/usr/bin/env python3
"""Calibrate generated scale-retrieval cases against real harness retrievers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[6]
EVAL_DIR = ROOT / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from memeval.stores.tests.scale_retrieval.helpers import (  # noqa: E402
    CHALLENGE,
    CURRENT_CELL_NAMES,
    DROP_REASONS,
    LENSES,
    MatrixCell,
    ScaleCase,
    Skip,
    case_to_record,
    close_cells,
    iter_matrix_cells,
    load_cases,
    load_items,
    manifest_drop_table,
    mrr_at_k,
    recall_at_k,
    skip_cells,
    write_jsonl,
)


DEPTH = 100
EVAL_K = 10
MRR_MARGIN = 1e-9


def _rank(ids: list[str], gold: tuple[str, ...]) -> Optional[int]:
    gold_set = set(gold)
    for i, item_id in enumerate(ids):
        if item_id in gold_set:
            return i
    return None


def _search(cell: MatrixCell, case: ScaleCase, *, k: int, force_as_of: Any = "case") -> list[str]:
    as_of = case.as_of if force_as_of == "case" else force_as_of
    return [hit.item_id for hit in cell.store.search(case.query, k=k, as_of=as_of)]


def _gate_metrics(ids: list[str], case: ScaleCase) -> dict[str, float]:
    return {
        f"recall@{EVAL_K}": recall_at_k(ids, case.gold_primary_ids, EVAL_K),
        f"MRR@{EVAL_K}": mrr_at_k(ids, case.gold_primary_ids, EVAL_K),
    }


def _target_beats_floor(target: dict[str, float], floor: dict[str, float]) -> bool:
    target_recall = target[f"recall@{EVAL_K}"]
    floor_recall = floor[f"recall@{EVAL_K}"]
    if target_recall > floor_recall:
        return True
    if target_recall == floor_recall:
        return target[f"MRR@{EVAL_K}"] > floor[f"MRR@{EVAL_K}"] + MRR_MARGIN
    return False


def _empty_lens_counts() -> dict[str, Any]:
    return {
        "generated": 0,
        "retained": 0,
        "dropped": 0,
        "dropped_by_reason": {reason: 0 for reason in DROP_REASONS},
    }


def _drop(manifest: dict[str, Any], case: ScaleCase, reason: str) -> None:
    manifest["lenses"][case.lens]["dropped"] += 1
    manifest["lenses"][case.lens]["dropped_by_reason"][reason] += 1
    manifest["totals"]["dropped"] += 1
    manifest["totals"]["dropped_by_reason"][reason] += 1


def _retain(manifest: dict[str, Any], case: ScaleCase) -> None:
    manifest["lenses"][case.lens]["retained"] += 1
    manifest["totals"]["retained"] += 1


def _classify_case(
    case: ScaleCase,
    *,
    by_id: dict[str, Any],
    cells: dict[str, MatrixCell],
) -> tuple[Optional[str], dict[str, Any]]:
    unknown = [item_id for item_id in case.gold_primary_ids if item_id not in by_id]
    if unknown:
        return "unknown_gold", {"unknown_gold_ids": unknown}
    future = [
        item_id for item_id in case.gold_primary_ids
        if case.as_of is not None and by_id[item_id].timestamp > case.as_of
    ]
    if future:
        return "future_gold", {"future_gold_ids": future}

    target_cell = cells.get(case.target)
    if target_cell is None:
        return "unsolved_target", {"target": case.target, "reason": "target cell unavailable offline"}

    target_ids = _search(target_cell, case, k=DEPTH)
    target_rank = _rank(target_ids, case.gold_primary_ids)
    target_metrics = _gate_metrics(target_ids, case)
    calibration: dict[str, Any] = {
        "target": case.target,
        "target_rank": target_rank,
        "target_top10": target_ids[:10],
        "target_metrics": target_metrics,
    }

    ambiguous_ids = tuple(str(x) for x in case.calibration.get("ambiguous_ids", ()))
    if ambiguous_ids and any(item_id in target_ids[:10] for item_id in ambiguous_ids):
        calibration["ambiguous_ids"] = list(ambiguous_ids)
        return "ambiguous_gold", calibration

    if case.kind == CHALLENGE and case.floor:
        floor_name = case.floor
        force_as_of: Any = "case"
        if floor_name.endswith("_unfiltered"):
            floor_name = floor_name.removesuffix("_unfiltered")
            force_as_of = None
        floor_cell = cells.get(floor_name)
        if floor_cell is not None:
            floor_ids = _search(floor_cell, case, k=DEPTH, force_as_of=force_as_of)
            floor_rank = _rank(floor_ids, case.gold_primary_ids)
            floor_metrics = _gate_metrics(floor_ids, case)
            calibration.update({
                "floor": case.floor,
                "floor_rank": floor_rank,
                "floor_top10": floor_ids[:10],
                "floor_metrics": floor_metrics,
            })
            if floor_rank is not None and floor_rank < case.floor_k:
                return "trivial_floor", calibration
            if not _target_beats_floor(target_metrics, floor_metrics):
                return "floor_not_beaten", calibration

    if target_rank is None:
        return "unsolved_target", calibration
    return None, calibration


def calibrate(
    *,
    quality_path: Path,
    case_path: Path,
    filler_path: Path | None,
    out_cases: Path,
    manifest_path: Path,
    tmp_root: Path,
) -> dict[str, Any]:
    quality = load_items(quality_path)
    filler = load_items(filler_path) if filler_path is not None and filler_path.exists() else []
    cases = load_cases(case_path)
    all_items = quality + filler
    by_id = {item.item_id: item for item in quality}
    cells = iter_matrix_cells(all_items, tmp_root, include_skips=False, live=False)
    runnable = {cell.name: cell for cell in cells if isinstance(cell, MatrixCell)}
    retained: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {
        "mode": "offline",
        "generated_at": "deterministic-offline-smoke",
        "search_depth": DEPTH,
        "quality_count": len(quality),
        "filler_count": len(filler),
        "candidate_count": len(cases),
        "drop_reasons": list(DROP_REASONS),
        "lenses": {lens: _empty_lens_counts() for lens in LENSES},
        "totals": _empty_lens_counts(),
        "matrix": {
            "current_cells": list(CURRENT_CELL_NAMES),
            "skipped_cells": [
                {"name": skip.name, "reason": skip.reason, **skip.columns}
                for skip in skip_cells()
            ],
        },
    }
    manifest["totals"]["generated"] = len(cases)
    try:
        for case in cases:
            if case.lens not in manifest["lenses"]:
                raise ValueError(f"{case.case_id}: unknown lens {case.lens!r}")
            manifest["lenses"][case.lens]["generated"] += 1
            reason, calibration = _classify_case(case, by_id=by_id, cells=runnable)
            record = case_to_record(case)
            record["calibration"] = {**case.calibration, **calibration}
            if reason is None:
                record["calibration"]["status"] = "retained"
                retained.append(record)
                _retain(manifest, case)
            else:
                record["calibration"]["status"] = "dropped"
                record["calibration"]["drop_reason"] = reason
                _drop(manifest, case, reason)
        write_jsonl(out_cases, retained)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest
    finally:
        close_cells(cells)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quality", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--filler", type=Path)
    parser.add_argument("--out-cases", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tmp-root", type=Path, required=True)
    args = parser.parse_args()

    manifest = calibrate(
        quality_path=args.quality,
        case_path=args.cases,
        filler_path=args.filler,
        out_cases=args.out_cases,
        manifest_path=args.manifest,
        tmp_root=args.tmp_root,
    )
    print(manifest_drop_table(manifest))


if __name__ == "__main__":
    main()
