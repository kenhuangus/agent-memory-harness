"""Track 0 scale-retrieval smoke benchmark.

This is the CI-safe slice of the larger benchmark: small calibrated fixtures, real
offline stores, metric math, OKF body-link round-tripping, and anti-theater
calibration counts. Full 10k/20k filler runs and captained Voyage cells are run by
the tracked reporter tool, not by this unit test.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from memeval.okf import doc_to_memory_item, memory_item_to_doc
from memeval.schema import MemoryItem
from memeval.stores.tests.scale_retrieval.helpers import (
    CURRENT_CELL_NAMES,
    DROP_REASONS,
    LENSES,
    LOCAL_ANN_CELL_NAMES,
    MatrixCell,
    Skip,
    close_cells,
    evaluate_case,
    fixture_dir,
    iter_matrix_cells,
    load_cases,
    load_items,
    manifest_drop_table,
    mrr_at_k,
    ndcg_at_k,
    recall_at_k,
)


FIXTURES = fixture_dir()


def _sample_challenges_by_lens(cases, *, per_lens: int = 6):
    out = []
    for lens in LENSES:
        lens_cases = [
            case for case in cases
            if case.kind == "challenge" and case.lens == lens
        ]
        out.extend(lens_cases[:per_lens])
    return out


def _quality_subset_for_cases(items, cases, *, extra: int = 150):
    by_id = {item.item_id: item for item in items}
    fact_ids = set()
    direct_ids = set()
    for case in cases:
        for item_id in case.gold_primary_ids + case.distractor_ids:
            item = by_id.get(item_id)
            if item is None:
                continue
            direct_ids.add(item.item_id)
            fact_id = (item.metadata or {}).get("fact_id")
            if fact_id:
                fact_ids.add(fact_id)
    selected = [
        item for item in items
        if item.item_id in direct_ids or (item.metadata or {}).get("fact_id") in fact_ids
    ]
    seen = {item.item_id for item in selected}
    for item in items:
        if len(selected) >= extra:
            break
        if item.item_id not in seen:
            selected.append(item)
            seen.add(item.item_id)
    return selected


class MetricHelperTests(unittest.TestCase):
    def test_recall_mrr_ndcg_worked_examples(self) -> None:
        ranked = ["wrong", "gold-a", "noise", "gold-b"]
        gold = ("gold-a", "gold-b")
        self.assertEqual(recall_at_k(ranked, gold, 1), 0.0)
        self.assertEqual(recall_at_k(ranked, gold, 2), 0.5)
        self.assertEqual(recall_at_k(ranked, gold, 10), 1.0)
        self.assertEqual(mrr_at_k(ranked, gold, 10), 0.5)

        gains = {"gold-a": 3.0, "gold-b": 1.0}
        expected = (3.0 / math.log2(3) + 1.0 / math.log2(5)) / (3.0 + 1.0 / math.log2(3))
        self.assertAlmostEqual(ndcg_at_k(ranked, gains, 10), expected)
        self.assertEqual(ndcg_at_k(["gold-a", "gold-b"], gains, 10), 1.0)


class OKFRoundTripTests(unittest.TestCase):
    def test_okf_links_survive_body_link_round_trip(self) -> None:
        item = MemoryItem(
            item_id="roundtrip-source",
            content=(
                "RoundTripSource records a relation in the markdown body: "
                "it [depends on](roundtrip-target.md)."
            ),
            metadata={
                "okf_title": "RoundTripSource",
                "okf_type": "Concept",
                "okf_links": [["depends on", "roundtrip-target.md"]],
            },
        )

        doc = memory_item_to_doc(item)
        frontmatter = doc.split("---", 2)[1]
        self.assertNotIn("okf_links", frontmatter)
        reparsed = doc_to_memory_item(doc)
        self.assertEqual(
            reparsed.metadata["okf_links"],
            [("depends on", "roundtrip-target.md")],
        )


class FixtureContractTests(unittest.TestCase):
    def test_fixture_files_are_well_formed(self) -> None:
        items = load_items(FIXTURES / "quality_items.jsonl")
        cases = load_cases(FIXTURES / "cases.retained.jsonl")
        self.assertGreaterEqual(len(items), 100)
        self.assertGreaterEqual(len(cases), 20)
        item_ids = {item.item_id for item in items}
        self.assertEqual(len(item_ids), len(items))
        for case in cases:
            self.assertIn(case.lens, LENSES)
            self.assertTrue(case.query.strip())
            for gold_id in case.gold_primary_ids:
                self.assertIn(gold_id, item_ids, f"{case.case_id}: unknown retained gold")
        temporal = [item for item in items if item.metadata.get("lens") == "temporal_versioned"]
        logical = {}
        for item in temporal:
            logical.setdefault(item.metadata["logical_id"], set()).add(item.item_id)
        self.assertTrue(any(len(ids) >= 3 for ids in logical.values()))

    def test_calibration_manifest_reports_generated_retained_and_dropped_by_reason(self) -> None:
        manifest = json.loads((FIXTURES / "calibration_manifest.offline.json").read_text())
        self.assertEqual(manifest["mode"], "offline")
        self.assertGreater(manifest["totals"]["generated"], manifest["totals"]["retained"])
        self.assertGreater(manifest["totals"]["dropped"], 0)
        self.assertEqual(tuple(manifest["drop_reasons"]), DROP_REASONS)
        for lens in LENSES:
            row = manifest["lenses"][lens]
            self.assertIn("generated", row)
            self.assertIn("retained", row)
            self.assertIn("dropped", row)
            self.assertEqual(set(row["dropped_by_reason"]), set(DROP_REASONS))
        for reason in DROP_REASONS:
            self.assertGreater(
                manifest["totals"]["dropped_by_reason"][reason],
                0,
                f"sample should exercise drop reason {reason}",
            )

    def test_manifest_drop_table_is_human_readable(self) -> None:
        manifest = json.loads((FIXTURES / "calibration_manifest.offline.json").read_text())
        table = manifest_drop_table(manifest)
        self.assertIn("semantic_divergence", table)
        self.assertIn("trivial_floor", table)
        self.assertIn("TOTAL", table)


class OfflineMatrixSmokeTests(unittest.TestCase):
    def test_small_retained_set_runs_over_offline_cells(self) -> None:
        all_quality = load_items(FIXTURES / "quality_items.jsonl")
        filler = load_items(FIXTURES / "filler_items.jsonl")[:100]
        cases = load_cases(FIXTURES / "cases.retained.jsonl")
        controls = [case for case in cases if case.kind == "control"]
        sample_challenges = _sample_challenges_by_lens(cases)
        sample = controls[:6] + sample_challenges
        quality = _quality_subset_for_cases(all_quality, sample)
        self.assertGreaterEqual(len(sample), 12)

        with tempfile.TemporaryDirectory() as tmp:
            cells = iter_matrix_cells(quality + filler, Path(tmp), include_skips=True, live=False)
            try:
                runnable = [cell for cell in cells if isinstance(cell, MatrixCell)]
                runnable_by_name = {cell.name: cell for cell in runnable}
                skipped = {cell.name: cell for cell in cells if isinstance(cell, Skip)}
                runnable_names = [cell.name for cell in runnable]
                self.assertEqual(runnable_names[:len(CURRENT_CELL_NAMES)], list(CURRENT_CELL_NAMES))
                self.assertTrue(
                    set(runnable_names) <= set(CURRENT_CELL_NAMES) | set(LOCAL_ANN_CELL_NAMES)
                )
                self.assertIn("accuracy_voyage", skipped)
                self.assertEqual(skipped["accuracy_voyage"].reason, "captained: MEMEVAL_LIVE unset")
                for name in LOCAL_ANN_CELL_NAMES:
                    if name not in runnable_names:
                        self.assertIn(name, skipped)

                target_solved_by_lens = {}
                for case in sample_challenges:
                    with self.subTest(case_id=case.case_id, lens=case.lens, target=case.target):
                        target = runnable_by_name.get(case.target)
                        self.assertIsNotNone(target, f"{case.case_id}: target cell is not runnable")
                        assert target is not None
                        target_result = evaluate_case(target.store, case, k=10)
                        self.assertGreater(
                            target_result["recall@10"],
                            0.0,
                            f"{case.case_id}: {case.target} did not return gold in top-10",
                        )
                        target_solved_by_lens.setdefault(case.lens, 0)
                        target_solved_by_lens[case.lens] += 1
                self.assertEqual(set(target_solved_by_lens), {case.lens for case in sample_challenges})

                seen_metric_rows = 0
                markdown_control_recall = []
                for cell in runnable:
                    for case in sample:
                        result = evaluate_case(cell.store, case, k=10)
                        for key in ("recall@1", "recall@5", "recall@10", "MRR@10", "nDCG@10"):
                            self.assertGreaterEqual(result[key], 0.0)
                            self.assertLessEqual(result[key], 1.0)
                        self.assertGreaterEqual(result["latency_ns"], 0)
                        seen_metric_rows += 1
                        if cell.name == "backend_markdown" and case.kind == "control":
                            markdown_control_recall.append(result["recall@1"])
                self.assertGreater(seen_metric_rows, 0)
                self.assertTrue(markdown_control_recall)
                self.assertEqual(sum(markdown_control_recall) / len(markdown_control_recall), 1.0)
            finally:
                close_cells(cells)


if __name__ == "__main__":
    unittest.main()
