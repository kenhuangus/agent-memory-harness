"""Offline tests for the MemoryAgentBench native evaluator.

Fully deterministic, no network, no LLM, no Docker: EchoAgent + EchoModel +
DeterministicJudge (the judge is unused by this benchmark but accepted). Asserts
the evaluator runs end-to-end and that every native metric + component slice is
computed and in range.

Run with the Windows Python:
    python -m pytest memeval/native/tests/test_native_memoryagentbench.py
or standalone:
    python memeval/native/tests/test_native_memoryagentbench.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Make ``memeval`` importable when run as a standalone script from anywhere.
_EVAL_ROOT = Path(__file__).resolve().parents[3]  # .../eval
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from memeval.native import (  # noqa: E402
    BenchmarkNativeReport,
    DeterministicJudge,
    PerTaskRecord,
    run_native,
)
from memeval.native.evaluators.memoryagentbench import (  # noqa: E402
    MemoryAgentBenchNativeEvaluator,
    em,
    rechunk_sessions,
    sub_em,
)
from memeval.schema import Benchmark, Session, Task, TaskKind  # noqa: E402

_FIXTURES = _EVAL_ROOT / "tests" / "fixtures"

# Canonical competency strings (mirror the loader / evaluator).
_AR = "accurate_retrieval"
_TTL = "test_time_learning"
_LRU = "long_range_understanding"
_CR = "conflict_resolution"


# --------------------------------------------------------------------------- #
# Inline four-competency fixture (so SubEM + EM and all four slices fire)
# --------------------------------------------------------------------------- #
def _inline_tasks() -> list[Task]:
    """One Task per competency, each with a session whose content carries the
    gold so EchoModel surfaces it on retrieval (memory-on)."""
    return [
        # Accurate Retrieval -> SubEM. Gold "Berlin" appears in the session.
        Task(
            task_id="mab_ar_1",
            benchmark=Benchmark.MEMORY_AGENT_BENCH,
            kind=TaskKind.QA,
            question="Which city did the user move to?",
            answer="Berlin",
            sessions=[
                Session("s_ar_0", "Talked about weekend hiking plans.", index=0),
                Session("s_ar_1", "The user relocated to Berlin for a new job.", index=1),
            ],
            competency=_AR,
            metadata={"subset": "EventQA", "acceptable_answers": ["Berlin"]},
        ),
        # Conflict Resolution -> SubEM, latest value wins. Gold "Rust".
        Task(
            task_id="mab_cr_1",
            benchmark=Benchmark.MEMORY_AGENT_BENCH,
            kind=TaskKind.QA,
            question="What is the user's current favorite language?",
            answer="Rust",
            sessions=[
                Session("s_cr_0", "Favorite language was Python.", index=0),
                Session("s_cr_1", "The user now prefers Rust for systems work.", index=1),
            ],
            competency=_CR,
            metadata={"subset": "fact_sh", "acceptable_answers": ["Rust"]},
        ),
        # Test-Time Learning -> strict EM. Gold label "banking".
        Task(
            task_id="mab_ttl_1",
            benchmark=Benchmark.MEMORY_AGENT_BENCH,
            kind=TaskKind.QA,
            question="Classify the intent.",
            answer="banking",
            sessions=[
                Session("s_ttl_0", "banking", index=0),
            ],
            competency=_TTL,
            metadata={"subset": "ICL_banking", "acceptable_answers": ["banking"]},
        ),
        # Long-Range Understanding -> strict EM. Gold "the butler".
        Task(
            task_id="mab_lru_1",
            benchmark=Benchmark.MEMORY_AGENT_BENCH,
            kind=TaskKind.QA,
            question="Who is the culprit?",
            answer="the butler",
            sessions=[
                Session("s_lru_0", "the butler", index=0),
            ],
            competency=_LRU,
            metadata={"subset": "detectiveQA", "acceptable_answers": ["the butler"]},
        ),
    ]


# --------------------------------------------------------------------------- #
# Native metric unit tests (paper-exact SubEM / EM behavior)
# --------------------------------------------------------------------------- #
def test_sub_em_is_raw_substring() -> None:
    # Raw substring containment (looser than whole-word qa_match).
    assert sub_em("the answer is 17 items", "7")  # "7" is inside "17"
    assert sub_em("relocated to Berlin recently", "Berlin")
    assert sub_em("Berlin", "berlin")  # case-insensitive via normalize
    assert not sub_em("London", "Berlin")
    # Empty gold matches only empty prediction.
    assert sub_em("", "")
    assert not sub_em("something", "")


def test_em_is_strict() -> None:
    assert em("banking", "banking")
    assert em("Banking", "banking")  # light lowercase canonicalization
    # Strict: a label-wrapped answer is WRONG (paper notes strict EM parsing).
    assert not em("label: 43", "43")
    assert not em("the answer is banking", "banking")
    assert not em("clinic", "banking")


def test_rechunk_keeps_small_sessions() -> None:
    sessions = [Session("a", "short one", index=0), Session("b", "short two", index=1)]
    out = rechunk_sessions(sessions, chunk_tokens=512, task_id="t")
    assert out == sessions  # already within budget -> unchanged


def test_rechunk_splits_long_session() -> None:
    long_content = " ".join(f"w{i}" for i in range(50))
    sessions = [Session("big", long_content, timestamp=10.0, index=0)]
    out = rechunk_sessions(sessions, chunk_tokens=8, task_id="t")
    assert len(out) > 1  # a 50-word session must split at ~6-word chunks
    assert all(s.timestamp == 10.0 for s in out)  # timestamps inherited
    # Re-joined content preserves the original token stream.
    assert " ".join(s.content for s in out).split() == long_content.split()


# --------------------------------------------------------------------------- #
# End-to-end offline run over the inline four-competency fixture
# --------------------------------------------------------------------------- #
def test_run_and_score_all_four_competencies() -> None:
    ev = MemoryAgentBenchNativeEvaluator()
    tasks = _inline_tasks()
    records = ev.run(
        tasks,
        agent_or_model=None,        # -> EchoAgent over EchoModel
        mode="echo",                # memory ON
        judge=DeterministicJudge(),  # accepted, unused by this benchmark
        chunk_tokens=512,
    )
    assert len(records) == len(tasks)
    assert all(isinstance(r, PerTaskRecord) for r in records)

    report = ev.score(records, tasks)
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "memoryagentbench"
    assert report.n_tasks == len(tasks)

    # Headline metrics present + in range.
    headline = report.metric("per_competency_mean_accuracy")
    assert headline is not None and 0.0 <= headline.value <= 1.0
    assert headline.better == "higher"
    micro = report.metric("question_accuracy_micro")
    assert micro is not None and 0.0 <= micro.value <= 1.0

    # All four component slices present, each with its native metric in range.
    for comp in (_AR, _TTL, _LRU, _CR):
        assert comp in report.components, f"missing component {comp}"
        cs = report.components[comp]
        assert cs.n == 1
        expected = "sub_em" if comp in (_AR, _CR) else "exact_match"
        m = cs.get(expected)
        assert m is not None, f"{comp} missing {expected} metric"
        assert 0.0 <= m.value <= 1.0

    # Headline equals the unweighted mean of the four competency means.
    comp_means = [
        report.components[c].get("sub_em" if c in (_AR, _CR) else "exact_match").value
        for c in (_AR, _TTL, _LRU, _CR)
    ]
    assert abs(headline.value - (sum(comp_means) / 4)) < 1e-9

    # Memory-ON EchoModel surfaces the gold for the AR SubEM competency (the
    # retrieved session content contains the gold string, and the AR query's
    # top hit is the evidence session). The CR competency's "latest value wins"
    # depends on the TEAM's retriever ranking (which we must not modify): the
    # offline lexical store may rank an older conflicting session first, so we
    # only assert its metric is computed and in range, not that it is 1.0.
    assert report.components[_AR].get("sub_em").value == 1.0
    cr_val = report.components[_CR].get("sub_em").value
    assert 0.0 <= cr_val <= 1.0

    # Fully JSON-serializable (the CLI dumps this).
    json.dumps(report.to_dict())


def test_memory_off_does_not_crash_and_scores_in_range() -> None:
    ev = MemoryAgentBenchNativeEvaluator()
    tasks = _inline_tasks()
    records = ev.run(tasks, agent_or_model=None, mode="off")  # memory OFF
    report = ev.score(records, tasks)
    headline = report.metric("per_competency_mean_accuracy")
    assert headline is not None and 0.0 <= headline.value <= 1.0
    json.dumps(report.to_dict())


# --------------------------------------------------------------------------- #
# End-to-end via the runner over the bundled fixture (registry resolution)
# --------------------------------------------------------------------------- #
def test_run_native_over_bundled_fixture() -> None:
    fixture = _FIXTURES / "memoryagentbench.json"
    assert fixture.exists(), f"missing fixture {fixture}"
    report = run_native(
        Benchmark.MEMORY_AGENT_BENCH,
        model_or_agent=None,
        mode="echo",
        path_or_id=str(fixture),
        limit=5,
    )
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "memoryagentbench"
    assert report.n_tasks >= 1
    # The bundled fixture canonicalizes to AR (EventQA) + CR (FactConsolidation).
    assert _AR in report.components or _CR in report.components
    headline = report.metric("per_competency_mean_accuracy")
    assert headline is not None and 0.0 <= headline.value <= 1.0
    # Provenance stamped by the runner; mode propagated.
    assert report.mode == "echo"
    assert report.metadata.get("source", "").endswith("memoryagentbench.json")
    json.dumps(report.to_dict())


# --------------------------------------------------------------------------- #
# Standalone runner
# --------------------------------------------------------------------------- #
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
