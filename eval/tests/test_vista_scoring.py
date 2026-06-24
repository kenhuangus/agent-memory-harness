"""Item 3 — reusable scoring additions (calibration, ECE, pass^k) tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent  # .../eval
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval import metrics as M  # noqa: E402
from memeval.schema import (  # noqa: E402
    Benchmark,
    MemoryItem,
    RetrievedItem,
    Task,
    TaskKind,
    Trajectory,
    TrajectoryStep,
)


def _traj_with_retrieval(task_id, retrieved_ids):
    traj = Trajectory(task_id=task_id, benchmark=Benchmark.VISTA, model="echo", memory_on=True)
    step = TrajectoryStep(step=0, kind="retrieve")
    for i, rid in enumerate(retrieved_ids):
        step.retrieved.append(
            RetrievedItem(item=MemoryItem(item_id=rid, content=rid), score=1.0 - i * 0.1, rank=i)
        )
    traj.steps.append(step)
    return traj


class RetrievalCalibrationTest(unittest.TestCase):
    def test_precision_recall_f1(self) -> None:
        task = Task(task_id="t1", benchmark=Benchmark.VISTA, kind=TaskKind.QA,
                    question="q", gold_memory_ids=["g1", "g2"])
        # retrieved g1 (gold), n1 (noise); missed g2 -> tp=1, fp=1, fn=1.
        traj = _traj_with_retrieval("t1", ["g1", "n1"])
        out = M.retrieval_calibration([traj], [task])
        self.assertAlmostEqual(out["precision"], 0.5)
        self.assertAlmostEqual(out["recall"], 0.5)
        self.assertAlmostEqual(out["f1"], 0.5)

    def test_perfect_retrieval(self) -> None:
        task = Task(task_id="t1", benchmark=Benchmark.VISTA, kind=TaskKind.QA,
                    question="q", gold_memory_ids=["g1"])
        traj = _traj_with_retrieval("t1", ["g1"])
        out = M.retrieval_calibration([traj], [task])
        self.assertEqual(out["precision"], 1.0)
        self.assertEqual(out["recall"], 1.0)
        self.assertEqual(out["f1"], 1.0)

    def test_existing_metrics_intact(self) -> None:
        # backward-compat: relevancy / compute_metrics still work unchanged.
        task = Task(task_id="t1", benchmark=Benchmark.VISTA, kind=TaskKind.QA,
                    question="q", gold_memory_ids=["g1"])
        traj = _traj_with_retrieval("t1", ["g1", "n1"])
        _sim, prec = M.relevancy([traj], [task])
        self.assertAlmostEqual(prec, 0.5)


class CalibrationTest(unittest.TestCase):
    def test_perfect_calibration_low_ece(self) -> None:
        conf, out = [], []
        for i in range(10):
            c = (i + 0.5) / 10
            for k in range(10):
                conf.append(c)
                out.append(1.0 if k < round(c * 10) else 0.0)
        self.assertLessEqual(M.ece(conf, out), 0.06)

    def test_overconfident_high_ece(self) -> None:
        conf = [0.95] * 100
        out = [1.0 if k < 60 else 0.0 for k in range(100)]  # acc 0.60
        self.assertGreater(M.ece(conf, out), 0.3)
        self.assertGreater(M.brier(conf, out), 0.0)

    def test_empty_is_zero(self) -> None:
        self.assertEqual(M.ece([], []), 0.0)
        self.assertEqual(M.brier([], []), 0.0)
        self.assertEqual(M.mce([], []), 0.0)


class NativeMetricsLedgerTest(unittest.TestCase):
    """results.py surfaces native metrics into the ledger row for the Results UI."""

    def _row(self, bench_id, mode):
        from memeval.loaders import get_loader
        from memeval.native.registry import get_native_evaluator
        from memeval.results import result_record
        from memeval.schema import Benchmark as B, Metrics, ModelConfig, RunResult

        if bench_id == "vista":
            tasks = get_loader(bench_id).load(None, split=None, limit=None)
        else:
            tasks = get_loader(bench_id).load(None, limit=6)
        ev = get_native_evaluator(bench_id)
        rep = ev.score(ev.run(tasks, mode=mode), tasks)
        rr = RunResult(
            benchmark=B.from_str(bench_id),
            config=ModelConfig(name="echo", memory=True, label="echo+mem"),
            metrics=Metrics(accuracy=0.0, n=len(tasks)),
            n_tasks=len(tasks),
            metadata={"native_report": rep, "mode": mode},
        )
        return result_record(rr, timestamp="2026-06-24T16:00:00+00:00")

    def test_vista_native_block_has_safety_and_poison(self) -> None:
        row = self._row("vista", "plugin")
        self.assertIn("native", row)
        keys = set(row["native"]["metrics"].keys())
        for required in ("poisoning_resistance", "targeted_asr",
                         "gold_retrieval_f1", "self_improvement_safety"):
            self.assertIn(required, keys)
        asr = row["native"]["metrics"]["targeted_asr"]
        self.assertEqual(asr["better"], "lower")
        self.assertIsNotNone(asr["value"])

    def test_swe_bench_cl_native_block_has_cl_suite(self) -> None:
        row = self._row("swe_bench_cl", "continual")
        self.assertIn("native", row)
        keys = set(row["native"]["metrics"].keys())
        for required in ("accuracy", "forgetting", "backward_transfer",
                         "forward_transfer", "aulc", "cl_plasticity"):
            self.assertIn(required, keys)
        self.assertEqual(row["native"]["metrics"]["forgetting"]["better"], "lower")

    def test_no_native_report_degrades_gracefully(self) -> None:
        from memeval.results import result_record
        from memeval.schema import Benchmark as B, Metrics, ModelConfig, RunResult

        rr = RunResult(
            benchmark=B.VISTA,
            config=ModelConfig(name="echo", memory=True),
            metrics=Metrics(accuracy=0.0),
            n_tasks=0,
            metadata={},  # no native_report -> no "native" key (UI shows n/a)
        )
        row = result_record(rr, timestamp="2026-06-24T16:00:00+00:00")
        self.assertNotIn("native", row)


class PassHatKTest(unittest.TestCase):
    def test_all_pass(self) -> None:
        self.assertEqual(M.pass_hat_k([True, True, True], 2), 1.0)

    def test_mixed(self) -> None:
        # windows of size 2 over [T,F,T]: (T,F)=fail,(F,T)=fail -> 0/2.
        self.assertEqual(M.pass_hat_k([True, False, True], 2), 0.0)
        # size 1: 2/3 pass.
        self.assertAlmostEqual(M.pass_hat_k([True, False, True], 1), 2 / 3)

    def test_guards(self) -> None:
        with self.assertRaises(ValueError):
            M.pass_hat_k([True], 0)
        with self.assertRaises(ValueError):
            M.pass_hat_k([True], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
