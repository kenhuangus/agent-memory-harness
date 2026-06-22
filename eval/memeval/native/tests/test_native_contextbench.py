"""Offline tests for the ContextBench native evaluator.

Proves the evaluator runs end-to-end fully offline (EchoAgent + EchoModel +
per-group InMemoryStore + DeterministicJudge — no network, no Docker, no LLM)
and that every native metric + component slice is computed and in range.

ContextBench is scored on **in-task context retrieval** quality at file / block /
line granularity (recall / precision / F1) + efficiency, stratified by language;
resolve_rate is a Docker-gated secondary signal that must DEGRADE (n=0), never
hard-fail, offline.

Run offline with the Windows Python:
    python -m pytest memeval/native/tests/test_native_contextbench.py
or standalone:
    python memeval/native/tests/test_native_contextbench.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make ``memeval`` importable when run as a standalone script from anywhere.
_EVAL_ROOT = Path(__file__).resolve().parents[3]  # .../eval
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from memeval.loaders import get_loader  # noqa: E402
from memeval.native import (  # noqa: E402
    BenchmarkNativeReport,
    DeterministicJudge,
    run_native,
)
from memeval.native.evaluators.contextbench import (  # noqa: E402
    ContextBenchNativeEvaluator,
    _gold_sets,
    _predicted_sets,
)
from memeval.schema import Benchmark  # noqa: E402

_FIXTURES = _EVAL_ROOT / "tests" / "fixtures"
_FIXTURE = _FIXTURES / "contextbench.json"

_GRANS = ("file", "block", "line")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_tasks(limit=None):
    return get_loader(Benchmark.CONTEXTBENCH).load(str(_FIXTURE), limit=limit)


def _in_unit(x: float) -> bool:
    return 0.0 - 1e-9 <= x <= 1.0 + 1e-9


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_fixture_present() -> None:
    assert _FIXTURE.exists(), f"missing fixture {_FIXTURE}"
    tasks = _load_tasks()
    assert len(tasks) >= 2
    # Each task carries gold spans (sessions) and gold ids.
    for t in tasks:
        assert t.kind.value == "code"
        assert t.gold_memory_ids, f"{t.task_id} has no gold spans"
        assert t.sessions


def test_gold_and_predicted_sets_well_formed() -> None:
    """_gold_sets/_predicted_sets produce the three granularities, all derivable."""
    tasks = _load_tasks()
    ev = ContextBenchNativeEvaluator()
    records = ev.run(tasks, mode="builtin")  # memory-on -> real retrieval
    by_id = {t.task_id: t for t in tasks}
    for r in records:
        task = by_id[r.task_id]
        gold = _gold_sets(task)
        pred = _predicted_sets(r, task)
        for g in _GRANS:
            assert g in gold and g in pred
        # gold blocks == gold ids; gold files/lines non-empty for these fixtures.
        assert gold["block"] == set(task.gold_memory_ids)
        assert gold["file"], f"{task.task_id} produced no gold files"
        assert gold["line"], f"{task.task_id} produced no gold lines"


def test_score_memory_on_metrics_in_range() -> None:
    """Memory-on run: every headline metric + component computed and in range."""
    tasks = _load_tasks()
    ev = ContextBenchNativeEvaluator()
    records = ev.run(tasks, mode="builtin")
    report = ev.score(records, tasks)

    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "contextbench"
    assert report.n_tasks == len(tasks)

    # Headline recall/precision/F1 per granularity, all in [0,1].
    for g in _GRANS:
        for m in (f"{g}_recall", f"{g}_precision", f"{g}_f1"):
            met = report.metric(m)
            assert met is not None, f"missing headline metric {m}"
            assert _in_unit(met.value), f"{m}={met.value} out of [0,1]"
            assert met.n == len(tasks)

    # With memory on, EchoAgent retrieves the gold spans, so file/block recall
    # should be positive (gold IS in the store and surfaces for these tasks).
    assert report.metric("block_recall").value > 0.0
    assert report.metric("file_recall").value > 0.0

    # efficiency present, >= 0, flagged lower-is-better.
    eff = report.metric("efficiency")
    assert eff is not None and eff.value >= 0.0 and eff.better == "lower"

    # avg_retrieved present and non-negative.
    avg = report.metric("avg_retrieved")
    assert avg is not None and avg.value >= 0.0

    # resolve_rate: secondary, skipped offline (no Docker grader) -> n == 0.
    rr = report.metric("resolve_rate")
    assert rr is not None and rr.n == 0 and rr.value == 0.0
    assert rr.metadata.get("secondary") is True

    # Components: one per granularity + one per language; every metric in range.
    for g in _GRANS:
        comp = report.components.get(f"granularity:{g}")
        assert comp is not None, f"missing component granularity:{g}"
        assert comp.get(f"{g}_f1") is not None
        for met in comp.metrics:
            assert _in_unit(met.value)

    langs = {t.competency for t in tasks}
    for lang in langs:
        comp = report.components.get(f"language:{lang}")
        assert comp is not None, f"missing component language:{lang}"
        assert comp.n >= 1
        for met in comp.metrics:
            assert _in_unit(met.value)

    # Fully JSON-serializable (the CLI dumps this).
    json.dumps(report.to_dict())


def test_score_memory_off_zero_retrieval() -> None:
    """Memory-off baseline: no retrieval -> zero recall, metrics still computed."""
    tasks = _load_tasks()
    ev = ContextBenchNativeEvaluator()
    records = ev.run(tasks, mode="off")
    report = ev.score(records, tasks)

    assert report.mode == "off"
    assert report.n_tasks == len(tasks)
    # Nothing retrieved -> recall is 0 at every granularity.
    for g in _GRANS:
        assert report.metric(f"{g}_recall").value == 0.0
    # Headline metrics still all present and in range.
    for g in _GRANS:
        for m in (f"{g}_recall", f"{g}_precision", f"{g}_f1"):
            assert _in_unit(report.metric(m).value)
    json.dumps(report.to_dict())


def test_run_native_end_to_end_offline() -> None:
    """Full offline path via run_native: loader + evaluator + EchoAgent + judge."""
    report = run_native(
        Benchmark.CONTEXTBENCH,
        model_or_agent=None,      # -> EchoAgent over EchoModel
        mode="builtin",
        path_or_id=str(_FIXTURE),
        limit=5,
    )
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "contextbench"
    assert report.n_tasks >= 1
    # Runner stamps provenance.
    assert report.metadata.get("judge") == "deterministic"
    # Every granularity F1 present + in range.
    for g in _GRANS:
        met = report.metric(f"{g}_f1")
        assert met is not None and _in_unit(met.value)
    json.dumps(report.to_dict())


def test_judge_arg_ignored_but_accepted() -> None:
    """ContextBench is retrieval-scored; passing a judge must not break run()."""
    tasks = _load_tasks(limit=2)
    ev = ContextBenchNativeEvaluator()
    records = ev.run(tasks, mode="builtin", judge=DeterministicJudge())
    assert len(records) == len(tasks)
    report = ev.score(records, tasks)
    assert report.n_tasks == len(tasks)


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
