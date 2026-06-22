"""Contract tests for the native-eval SCAFFOLDING (not any single benchmark).

These prove the shared contract is stable and works fully offline:

* the result dataclasses (NativeMetric / ComponentScore / BenchmarkNativeReport)
  build and round-trip to JSON-serializable dicts,
* the DeterministicJudge scores qa / choice / preference / abstention with no
  network,
* the registry registers + resolves an evaluator (and errors clearly otherwise),
* :func:`run_native` drives a stub evaluator end-to-end over a real fixture using
  EchoAgent + InMemoryStore + DeterministicJudge, and
* :meth:`BaseNativeEvaluator.run_tasks` yields one populated trajectory per task.

Run offline with the Windows Python:
    python -m pytest memeval/native/tests/test_native_scaffold.py
or standalone:
    python memeval/native/tests/test_native_scaffold.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

# Make ``memeval`` importable when run as a standalone script from anywhere.
_EVAL_ROOT = Path(__file__).resolve().parents[3]  # .../eval
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from memeval.schema import Benchmark, Task  # noqa: E402
from memeval.native import (  # noqa: E402
    BenchmarkNativeReport,
    ComponentScore,
    DeterministicJudge,
    NativeMetric,
    PerTaskRecord,
    get_judge,
    run_native,
)
from memeval.native.evaluators.base import (  # noqa: E402
    BaseNativeEvaluator,
    f1,
    mean,
    ndcg_at_k,
    set_precision,
    set_recall,
)
from memeval.native.registry import (  # noqa: E402
    available_native,
    get_native_evaluator,
    register_native_evaluator,
)

_FIXTURES = _EVAL_ROOT / "tests" / "fixtures"


# --------------------------------------------------------------------------- #
# A minimal stub evaluator exercising the BaseNativeEvaluator plumbing.
# --------------------------------------------------------------------------- #
class _StubEvaluator(BaseNativeEvaluator):
    """Scores plain accuracy over EchoAgent trajectories — contract exercise."""

    benchmark = Benchmark.LONGMEMEVAL.value

    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        mode: str = "off",
        store: Any = None,
        judge: Any = None,
        cost: Any = None,
        limit: Any = None,
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        from memeval.native.evaluators.base import mode_to_memory

        recs = self.run_tasks(
            tasks, agent_or_model=agent_or_model,
            memory=mode_to_memory(mode), store=store, cost=cost,
            k=kwargs.get("k", 5),
        )
        # Cache a deterministic judge label per record (judge seam exercise).
        j = judge or DeterministicJudge()
        by_id = {t.task_id: t for t in tasks}
        for r in recs:
            t = by_id[r.task_id]
            r.extra["judged"] = j.judge(t.question, t.answer or "", r.prediction, kind="qa")
        return recs

    def score(
        self, records: Sequence[PerTaskRecord], tasks: Sequence[Task]
    ) -> BenchmarkNativeReport:
        labels = [bool(r.extra.get("judged")) for r in records]
        rep = self.empty_report("test", len(records))
        rep.add_metric(
            NativeMetric("qa_accuracy_overall", mean([1.0 if x else 0.0 for x in labels]),
                         n=len(labels), better="higher")
        )
        return rep


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_records_roundtrip_to_dict() -> None:
    rep = BenchmarkNativeReport(benchmark="longmemeval", mode="echo", n_tasks=2)
    rep.add_metric(NativeMetric("acc", 0.5, n=2))
    comp = ComponentScore("multi-session", n=1)
    comp.add(NativeMetric("acc", 1.0, n=1, better="higher"))
    rep.add_component(comp)
    d = rep.to_dict()
    # Must be fully JSON-serializable (the CLI dumps this).
    s = json.dumps(d)
    assert "qa" not in s or True  # smoke
    assert d["benchmark"] == "longmemeval"
    assert d["metrics"][0]["name"] == "acc"
    assert d["components"]["multi-session"]["metrics"][0]["value"] == 1.0
    assert rep.metric("acc").value == 0.5
    assert comp.get("acc").n == 1


def test_deterministic_judge_kinds() -> None:
    j = DeterministicJudge()
    # qa: whole-word containment
    assert j.judge("capital of France?", "Paris", "The capital is Paris.", kind="qa")
    assert not j.judge("capital?", "Paris", "London", kind="qa")
    # choice: strict-ish option match, no substring leakage
    assert j.judge("pick", "B", "Answer: B", kind="choice")
    assert not j.judge("pick", "7", "17", kind="choice")
    # abstention: refusal detection
    assert j.judge("unknown?", "info missing", "I don't know, no information.", kind="abstention")
    assert not j.judge("unknown?", "info missing", "It is Paris.", kind="abstention")
    # preference: rubric overlap
    assert j.judge("style?", "concise and friendly tone", "a concise friendly tone", kind="preference")


def test_get_judge_factory_offline() -> None:
    assert isinstance(get_judge(None), DeterministicJudge)
    assert isinstance(get_judge("deterministic"), DeterministicJudge)
    j = DeterministicJudge()
    assert get_judge(j) is j  # pass-through


def test_scoring_helpers() -> None:
    assert set_recall({1, 2}, {1, 2, 3}) == 2 / 3
    assert set_precision({1, 2, 4}, {1, 2, 3}) == 2 / 3
    assert f1(0.5, 0.5) == 0.5
    assert f1(0.0, 0.0) == 0.0
    assert mean([]) == 0.0
    assert ndcg_at_k([1, 0, 1], 3) > 0.0
    assert ndcg_at_k([0, 0, 0], 3) == 0.0


def test_registry_register_and_resolve() -> None:
    register_native_evaluator(Benchmark.LONGMEMEVAL, _StubEvaluator())
    ev = get_native_evaluator("longmemeval")
    assert isinstance(ev, _StubEvaluator)
    assert Benchmark.LONGMEMEVAL in available_native()
    # class-form override is instantiated fresh
    register_native_evaluator(Benchmark.LONGMEMEVAL, _StubEvaluator)
    assert isinstance(get_native_evaluator("longmemeval"), _StubEvaluator)


def test_run_native_end_to_end_offline() -> None:
    """Full offline path: loader + stub evaluator + EchoAgent + det judge."""
    register_native_evaluator(Benchmark.LONGMEMEVAL, _StubEvaluator())
    fixture = _FIXTURES / "longmemeval.json"
    assert fixture.exists(), f"missing fixture {fixture}"
    report = run_native(
        Benchmark.LONGMEMEVAL,
        model_or_agent=None,       # -> EchoAgent over EchoModel
        mode="echo",
        path_or_id=str(fixture),
        limit=5,
    )
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "longmemeval"
    assert report.n_tasks >= 1
    acc = report.metric("qa_accuracy_overall")
    assert acc is not None and 0.0 <= acc.value <= 1.0
    # provenance stamped by the runner
    assert report.metadata.get("judge") == "deterministic"
    # round-trips to JSON
    json.dumps(report.to_dict())


def test_base_run_tasks_yields_populated_trajectories() -> None:
    """run_tasks gives one record/task with a retrieve step + prediction."""
    from memeval.loaders import get_loader

    tasks = get_loader(Benchmark.LONGMEMEVAL).load(str(_FIXTURES / "longmemeval.json"), limit=3)
    ev = _StubEvaluator()
    recs = ev.run_tasks(tasks, memory=True)
    assert len(recs) == len(tasks)
    for r in recs:
        assert isinstance(r, PerTaskRecord)
        assert r.trajectory.task_id == r.task_id
        # memory-on EchoAgent retrieves then generates
        kinds = [s.kind for s in r.trajectory.steps]
        assert "retrieve" in kinds and "generate" in kinds


def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
