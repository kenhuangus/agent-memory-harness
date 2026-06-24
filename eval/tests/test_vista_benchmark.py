"""Item 2 — VISTA as the 2nd benchmark: loader + native evaluator offline tests."""

from __future__ import annotations

import builtins
import sys
import unittest
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent  # .../eval
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from memeval.loaders import get_loader  # noqa: E402
from memeval.loaders.vista import _VENDORED, VistaLoader  # noqa: E402
from memeval.native.registry import get_native_evaluator  # noqa: E402
from memeval.schema import Benchmark, TaskKind  # noqa: E402

_REMOTE_MODULES = ("datasets", "huggingface_hub", "requests")


class _NoRemoteImports:
    def __enter__(self):
        self._real = builtins.__import__

        def _guard(name, *a, **k):
            if name.split(".", 1)[0] in _REMOTE_MODULES:
                raise AssertionError(f"vendored load must not import {name!r}")
            return self._real(name, *a, **k)

        builtins.__import__ = _guard
        return self

    def __exit__(self, *exc):
        builtins.__import__ = self._real


class VistaLoaderTest(unittest.TestCase):
    def test_vendored_corpus_exists_and_packaged(self) -> None:
        self.assertTrue(_VENDORED.is_file(), f"missing {_VENDORED}")
        self.assertEqual(_VENDORED.parent.name, "vista")
        self.assertEqual(_VENDORED.parent.parent.name, "data")

    def test_registry_resolves_vista(self) -> None:
        self.assertIsInstance(get_loader("vista"), VistaLoader)
        self.assertIs(Benchmark.from_str("vista_bench"), Benchmark.VISTA)

    def test_offline_load_maps_journeys(self) -> None:
        with _NoRemoteImports():
            tasks = get_loader("vista").load(None, split=None, limit=None)
        self.assertEqual(len(tasks), 6)
        for t in tasks:
            self.assertIs(t.benchmark, Benchmark.VISTA)
            self.assertIs(t.kind, TaskKind.QA)
            self.assertTrue(t.question)  # the intent
            self.assertTrue(t.sessions)  # event-trace -> sessions

    def test_event_types_covered(self) -> None:
        with _NoRemoteImports():
            tasks = get_loader("vista").load(None, split=None, limit=None)
        seen = set()
        for t in tasks:
            for s in t.sessions:
                seen.add(s.metadata.get("event_type"))
        for required in ("injection", "drift", "slow_burn"):
            self.assertIn(required, seen)

    def test_injection_is_poison_not_gold(self) -> None:
        with _NoRemoteImports():
            tasks = get_loader("vista").load(None, split=None, limit=None)
        for t in tasks:
            poison = set(t.metadata["poison_memory_ids"])
            # poisoned ids are never in gold
            self.assertEqual(poison & set(t.gold_memory_ids), set())
            # at least one injection per journey
            self.assertTrue(poison)

    def test_limit_respected(self) -> None:
        with _NoRemoteImports():
            tasks = get_loader("vista").load(None, split=None, limit=2)
        self.assertEqual(len(tasks), 2)


class VistaEvaluatorTest(unittest.TestCase):
    def _tasks(self):
        with _NoRemoteImports():
            return get_loader("vista").load(None, split=None, limit=None)

    def test_run_and_score_deterministic(self) -> None:
        ev = get_native_evaluator("vista")
        tasks = self._tasks()
        recs = ev.run(tasks, mode="plugin")
        rep1 = ev.score(recs, tasks)
        rep2 = ev.score(recs, tasks)
        self.assertEqual(rep1.to_dict(), rep2.to_dict())

    def test_report_has_expected_metrics(self) -> None:
        ev = get_native_evaluator("vista")
        tasks = self._tasks()
        rep = ev.score(ev.run(tasks, mode="plugin"), tasks)
        names = {m.name for m in rep.metrics}
        for required in ("poisoning_resistance", "targeted_asr",
                         "gold_retrieval_f1", "adaptation_rate"):
            self.assertIn(required, names)
        asr = rep.metric("targeted_asr")
        self.assertEqual(asr.better, "lower")
        self.assertTrue(0.0 <= asr.value <= 1.0)

    def test_poison_leak_is_detected(self) -> None:
        # If the poisoned injection memory IS retrieved, targeted_asr must rise.
        # EchoAgent retrieves by the query; force a leak by checking the helper:
        from memeval.safety import belief_from_journey
        ev = get_native_evaluator("vista")
        tasks = self._tasks()
        journey = ev._journey_of(tasks[0])
        belief = belief_from_journey(journey)
        # the injection payload contains a canary phrase
        inj = next(s for s in tasks[0].sessions
                   if s.metadata.get("event_type") == "injection")
        self.assertTrue(belief.matches(inj.content))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
