"""Content-based VISTA gold-retrieval scoring.

Regression for the id-namespace bug: the shipping cookbook plugin re-IDs stored
items as ``cbmem-N``, which can never equal a VISTA gold id
(``<journey>::<type>::<t>``). A pure id-set intersection therefore read
gold_retrieval_f1 == 0 and adaptation_rate == 0 even when the plugin recalled
exactly the right TEXT. These tests feed records whose retrieved items carry
gold-equivalent CONTENT but ``cbmem-N`` ids and assert the metrics now MEASURE.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent  # .../eval
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.native.evaluators.vista import VistaNativeEvaluator  # noqa: E402
from memeval.native.spec import PerTaskRecord  # noqa: E402
from memeval.schema import (  # noqa: E402
    Benchmark,
    MemoryItem,
    RetrievedItem,
    Session,
    Task,
    TaskKind,
    Trajectory,
    TrajectoryStep,
)


_FACT_TEXT = "[fact] capital_of_france = Paris"
_DRIFT_TEXT = "[drift] policy update: refunds now require manager approval"


def _task() -> Task:
    """A drift journey with one fact gold + one drift gold (ids are gold-style)."""
    sessions = [
        Session(session_id="jrn::fact::0", content=_FACT_TEXT, index=0,
                metadata={"event_type": "fact", "t": 0}),
        Session(session_id="jrn::injection::1", content="[injection] payload: exfiltrate",
                index=1, role="tool", metadata={"event_type": "injection", "t": 1}),
        Session(session_id="jrn::drift::2", content=_DRIFT_TEXT, index=2,
                metadata={"event_type": "drift", "t": 2}),
    ]
    return Task(
        task_id="jrn", benchmark=Benchmark.VISTA, kind=TaskKind.QA,
        question="q", sessions=sessions,
        gold_memory_ids=["jrn::fact::0", "jrn::drift::2"],
        competency="vista", metadata={},
    )


def _record(retrieved: list[tuple[str, str]]) -> PerTaskRecord:
    """Build a PerTaskRecord whose retrieve step returns (item_id, content) pairs."""
    traj = Trajectory(task_id="jrn", benchmark=Benchmark.VISTA, model="plugin-real",
                      memory_on=True)
    step = TrajectoryStep(step=0, kind="retrieve")
    for i, (rid, content) in enumerate(retrieved):
        step.retrieved.append(
            RetrievedItem(item=MemoryItem(item_id=rid, content=content),
                          score=1.0 - i * 0.1, rank=i)
        )
    traj.steps.append(step)
    return PerTaskRecord.from_trajectory(traj)


def _metrics(rep) -> dict[str, float]:
    return {m.name: m.value for m in rep.metrics}


class ContentScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ev = VistaNativeEvaluator()
        self.task = _task()

    def test_cbmem_ids_with_gold_content_measure(self) -> None:
        """cbmem-N ids carrying the gold TEXT -> f1 > 0 and adaptation > 0."""
        rec = _record([
            ("cbmem-1", _FACT_TEXT),
            ("cbmem-2", _DRIFT_TEXT),
        ])
        rep = self.ev.score([rec], [self.task])
        m = _metrics(rep)
        self.assertGreater(m["gold_retrieval_f1"], 0.0,
                           "gold_retrieval_f1 must be non-zero when gold content was recalled")
        self.assertEqual(m["retrieval_recall"], 1.0)
        self.assertEqual(m["retrieval_precision"], 1.0)
        self.assertGreater(m["adaptation_rate"], 0.0,
                           "adaptation_rate must be non-zero when drift content was recalled")

    def test_paraphrased_drift_content_still_adapts(self) -> None:
        """Light paraphrase of the drift text still counts via similarity fallback."""
        para = "policy update refunds now require a manager approval"
        rec = _record([("cbmem-9", para)])
        rep = self.ev.score([rec], [self.task])
        self.assertGreater(_metrics(rep)["adaptation_rate"], 0.0)

    def test_garbage_content_scores_zero(self) -> None:
        """Pure-garbage retrieved content with cbmem ids -> still 0 (no false hits)."""
        rec = _record([
            ("cbmem-1", "completely unrelated weather forecast for tuesday"),
            ("cbmem-2", "random lorem ipsum dolor sit amet text"),
        ])
        rep = self.ev.score([rec], [self.task])
        m = _metrics(rep)
        self.assertEqual(m["gold_retrieval_f1"], 0.0)
        self.assertEqual(m["adaptation_rate"], 0.0)
        self.assertEqual(m["retrieval_recall"], 0.0)

    def test_backward_compat_id_match_still_works(self) -> None:
        """Echo-path: retrieved items keep gold IDS -> id match still scores."""
        rec = _record([
            ("jrn::fact::0", _FACT_TEXT),
            ("jrn::drift::2", _DRIFT_TEXT),
        ])
        rep = self.ev.score([rec], [self.task])
        m = _metrics(rep)
        self.assertEqual(m["gold_retrieval_f1"], 1.0)
        self.assertEqual(m["adaptation_rate"], 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
