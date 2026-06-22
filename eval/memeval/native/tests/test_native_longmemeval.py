"""Offline tests for the LongMemEval native evaluator.

Fully deterministic: EchoAgent + EchoModel + per-task InMemoryStore +
DeterministicJudge — no network, no LLM, stdlib only. Runnable either under
pytest::

    python -m pytest memeval/native/tests/test_native_longmemeval.py

or as a plain script::

    python memeval/native/tests/test_native_longmemeval.py

Covers:
* end-to-end run via :func:`run_native` over the bundled fixture, asserting the
  headline metrics exist and are in range and the report round-trips to JSON;
* a richer inline fixture exercising all SIX native question types plus an
  abstention item, asserting every per-type component slice and the abstention
  component are computed and in range;
* the abstention judge path (a refusal prediction scores correct);
* the session-level retrieval recall/NDCG path (the gold session ranks first
  for the EchoAgent, so recall@k and ndcg@k are > 0 with memory on, and the
  abstention cohort is excluded from the retrieval means).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

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
from memeval.native.evaluators.longmemeval import (  # noqa: E402
    LongMemEvalNativeEvaluator,
    _judge_kind,
    _lme_dcg,
    _lme_ndcg,
    _ranked_session_ids,
)
from memeval.native.registry import register_native_evaluator
from memeval.schema import Benchmark  # noqa: E402

_FIXTURES = _EVAL_ROOT / "tests" / "fixtures"

# The six native answerable question types (hyphenated, as the report labels
# components) + the abstention component.
_TYPE_COMPONENTS = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)

_RECALL_KS = (1, 3, 5, 10, 50)


# --------------------------------------------------------------------------- #
# A richer inline fixture: one question per native type + one abstention item.
# The gold session's content carries the verbatim answer so the memory-on
# EchoAgent echoes it back and the DeterministicJudge scores it correct.
# --------------------------------------------------------------------------- #
def _row(qid: str, qtype: str, question: str, answer: str, gold_text: str) -> dict:
    """One LongMemEval-shaped row with a distractor + a gold evidence session."""
    return {
        "question_id": qid,
        "question_type": qtype,
        "question": question,
        "answer": answer,
        "question_date": "2023-09-01 12:00",
        "haystack_session_ids": [f"{qid}_distract", f"{qid}_gold"],
        "haystack_dates": ["2023-05-10 (Wed) 08:30", "2023-07-15 (Sat) 19:20"],
        "answer_session_ids": [f"{qid}_gold"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "Unrelated small talk about the weather."},
                {"role": "assistant", "content": "Sounds pleasant."},
            ],
            [
                {"role": "user", "content": gold_text},
                {"role": "assistant", "content": "Noted, thanks for sharing."},
            ],
        ],
    }


def _inline_rows() -> list[dict]:
    rows = [
        _row("lme_ssu_1", "single-session-user",
             "What city does the user live in?", "Denver",
             "I live in Denver and love the mountains."),
        _row("lme_ssa_1", "single-session-assistant",
             "What did the assistant recommend?", "yoga",
             "The assistant recommended yoga for stress relief."),
        _row("lme_ssp_1", "single-session-preference",
             "How does the user like replies?", "concise",
             "I prefer concise replies that get to the point."),
        _row("lme_ms_1", "multi-session",
             "What pet does the user own?", "dog",
             "I adopted a dog named Biscuit last month."),
        _row("lme_tr_1", "temporal-reasoning",
             "Where did the user vacation in summer?", "Lisbon",
             "We spent our summer vacation in Lisbon and loved it."),
        _row("lme_ku_1", "knowledge-update",
             "What is the user's current job title?", "manager",
             "I was just promoted to manager at the firm."),
    ]
    # Abstention item: answer absent from history; gold answer is a refusal phrase
    # so an abstaining prediction would score correct under the abstention judge.
    rows.append({
        "question_id": "lme_abs_1_abs",
        "question_type": "single-session-user",
        "question": "What is the user's blood type?",
        "answer": "The information is not available.",
        "question_date": "2023-09-02 09:00",
        "haystack_session_ids": ["lme_abs_1_only"],
        "haystack_dates": ["2023-08-01 (Tue) 11:00"],
        "answer_session_ids": [],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I went grocery shopping today."},
                {"role": "assistant", "content": "Hope you found everything."},
            ],
        ],
    })
    return rows


def _write_inline_fixture() -> str:
    fh = tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="lme_inline_", delete=False, encoding="utf-8"
    )
    json.dump(_inline_rows(), fh)
    fh.close()
    return fh.name


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_run_native_bundled_fixture_in_range() -> None:
    """End-to-end over the committed fixture: metrics exist + in range + JSON."""
    register_native_evaluator(Benchmark.LONGMEMEVAL, LongMemEvalNativeEvaluator())
    fixture = _FIXTURES / "longmemeval.json"
    assert fixture.exists(), f"missing fixture {fixture}"
    report = run_native(
        Benchmark.LONGMEMEVAL,
        model_or_agent=None,   # -> EchoAgent over EchoModel
        mode="echo",           # memory ON
        path_or_id=str(fixture),
    )
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "longmemeval"
    assert report.n_tasks >= 1

    overall = report.metric("qa_accuracy_overall")
    assert overall is not None and 0.0 <= overall.value <= 1.0
    assert report.metric("abstention_accuracy") is not None

    # All recall/ndcg @k headline metrics present and in range.
    for k in _RECALL_KS:
        rm = report.metric(f"answer_session_recall_at_{k}")
        nm = report.metric(f"answer_session_ndcg_at_{k}")
        assert rm is not None and 0.0 <= rm.value <= 1.0, f"recall@{k}"
        assert nm is not None and 0.0 <= nm.value <= 1.0, f"ndcg@{k}"

    # Fully JSON-serializable (the CLI dumps this).
    json.dumps(report.to_dict())


def test_all_question_type_components_and_abstention() -> None:
    """Richer inline fixture: every type component + abstention component scored."""
    register_native_evaluator(Benchmark.LONGMEMEVAL, LongMemEvalNativeEvaluator())
    path = _write_inline_fixture()
    try:
        report = run_native(
            Benchmark.LONGMEMEVAL,
            model_or_agent=None,
            mode="echo",
            path_or_id=path,
        )
    finally:
        Path(path).unlink(missing_ok=True)

    assert report.n_tasks == 7  # 6 answerable + 1 abstention

    # Every native question-type component exists, has its slice metric in range.
    for name in _TYPE_COMPONENTS:
        comp = report.components.get(name)
        assert comp is not None, f"missing component {name!r}"
        acc = comp.get("qa_accuracy")
        assert acc is not None and 0.0 <= acc.value <= 1.0

    # Faithful to print_qa_metrics.py: the _abs item (question_type
    # "single-session-user") is appended to its REAL type bucket UNCONDITIONALLY,
    # in addition to the abstention tally. So single-session-user has n==2 (its
    # own answerable question + the abstention item), every other type has n==1.
    assert report.components["single-session-user"].n == 2
    for name in _TYPE_COMPONENTS:
        if name == "single-session-user":
            continue
        assert report.components[name].n == 1, f"{name} should hold exactly 1 item"

    # Abstention component present AND ALSO double-counted in its type bucket.
    abst = report.components.get("abstention")
    assert abst is not None
    assert abst.n == 1
    assert abst.get("abstention_accuracy") is not None

    # Headline accuracy follows print_qa_metrics.py: the mean over ALL questions
    # (answerable + abstention), so n counts every trial (N=500 natively).
    overall = report.metric("qa_accuracy_overall")
    assert overall is not None
    assert overall.n == 7
    assert 0.0 <= overall.value <= 1.0

    # Task-averaged (macro) Accuracy also surfaced: mean over the six per-type
    # means (each type weighted equally). n == number of non-empty type buckets.
    macro = report.metric("qa_accuracy_task_averaged")
    assert macro is not None
    assert macro.n == 6  # all six native types are populated in this fixture
    assert 0.0 <= macro.value <= 1.0

    # Retrieval means computed over the 6 non-abstention questions only (the
    # abstention item has no gold session and is excluded).
    assert report.metadata.get("n_retrieval_scored") == 6
    rec5 = report.metric("answer_session_recall_at_5")
    assert rec5 is not None and rec5.n == 6
    # Only two sessions exist per question, so the gold session is always within
    # top-5: session-level recall@5 is exactly 1.0.
    assert rec5.value == 1.0
    nd5 = report.metric("answer_session_ndcg_at_5")
    assert nd5 is not None and nd5.value > 0.0


def test_abstention_judge_marks_refusal_correct() -> None:
    """A refusal prediction scores correct under the abstention judge path."""
    register_native_evaluator(Benchmark.LONGMEMEVAL, LongMemEvalNativeEvaluator())
    tasks = get_loader(Benchmark.LONGMEMEVAL).load(
        str(_FIXTURES / "longmemeval.json")
    )
    abs_tasks = [t for t in tasks if t.metadata.get("abstention")]
    assert abs_tasks, "fixture should contain an abstention task"

    ev = LongMemEvalNativeEvaluator()
    # run() routes abstention items through kind="abstention"; force the agent's
    # prediction to a refusal via a reply-fixed EchoModel so the label is correct.
    from memeval.agent import EchoAgent
    from memeval.models import EchoModel

    refusing_agent = EchoAgent(model=EchoModel(reply="I don't know, no information."))
    records = ev.run(
        abs_tasks, agent_or_model=refusing_agent, mode="echo",
        judge=DeterministicJudge(),
    )
    assert records and all(r.extra.get("judge_kind") == "abstention" for r in records)
    assert all(r.extra.get("label") is True for r in records)

    report = ev.score(records, abs_tasks)
    aa = report.metric("abstention_accuracy")
    assert aa is not None and aa.value == 1.0

    # Faithful to print_qa_metrics.py: the _abs item is ALSO counted in its real
    # question_type bucket (here single-session-user), not excluded from it. So
    # that one type holds the item; the other five types are empty.
    abs_types = {t.competency for t in abs_tasks}  # normalized form
    # The bundled abstention fixture is a single-session-user item.
    assert "single_session_user" in abs_types
    assert report.components["single-session-user"].n == len(abs_tasks)
    for name in _TYPE_COMPONENTS:
        if name == "single-session-user":
            continue
        comp = report.components.get(name)
        assert comp is None or comp.n == 0
    # And it appears in the dedicated abstention component too (double-counted).
    assert report.components["abstention"].n == len(abs_tasks)


def test_memory_off_recall_is_zero() -> None:
    """With memory OFF the agent retrieves nothing -> recall/ndcg are 0."""
    register_native_evaluator(Benchmark.LONGMEMEVAL, LongMemEvalNativeEvaluator())
    path = _write_inline_fixture()
    try:
        report = run_native(
            Benchmark.LONGMEMEVAL,
            model_or_agent=None,
            mode="off",  # memory OFF -> no retrieve hits
            path_or_id=path,
        )
    finally:
        Path(path).unlink(missing_ok=True)

    for k in _RECALL_KS:
        rm = report.metric(f"answer_session_recall_at_{k}")
        nm = report.metric(f"answer_session_ndcg_at_{k}")
        assert rm is not None and rm.value == 0.0, f"recall@{k} off"
        assert nm is not None and nm.value == 0.0, f"ndcg@{k} off"
    # report.mode reflects the memory-off run.
    assert report.mode in ("off", "")


def test_helpers_judge_kind_and_ranking() -> None:
    """Unit-level checks on the kind selector and the rank extractor."""
    tasks = get_loader(Benchmark.LONGMEMEVAL).load(
        str(_FIXTURES / "longmemeval.json")
    )
    by_id = {t.task_id: t for t in tasks}
    # temporal item -> distinct temporal_reasoning kind (off-by-one tolerance in
    # the LIVE prompt; offline it is still scored as plain QA); abstention ->
    # abstention.
    temporal = next(t for t in tasks if t.competency == "temporal_reasoning")
    assert _judge_kind(temporal) == "temporal_reasoning"
    abst = next(t for t in tasks if t.metadata.get("abstention"))
    assert _judge_kind(abst) == "abstention"
    # The deterministic judge treats temporal_reasoning/knowledge_update as QA.
    dj = DeterministicJudge()
    assert dj.judge("q", "Lisbon", "We went to Lisbon.", kind="temporal_reasoning") is True
    assert dj.judge("q", "manager", "Promoted to manager.", kind="knowledge_update") is True

    # _ranked_session_ids reads the last retrieve step in rank order.
    ev = LongMemEvalNativeEvaluator()
    recs = ev.run(tasks, mode="echo", judge=DeterministicJudge())
    rec = next(r for r in recs if r.task_id == temporal.task_id)
    ranked = _ranked_session_ids(rec)
    assert ranked, "memory-on retrieve step should surface session ids"
    # Every id must be one of the task's session ids (item_id == session_id).
    sess_ids = {s.session_id for s in by_id[temporal.task_id].sessions}
    assert set(ranked) <= sess_ids


def _multigold_record(task_id: str, gold_ids, retrieved_ids, label=True):
    """Build a PerTaskRecord with a single retrieve step over ``retrieved_ids``.

    ``retrieved_ids`` is best-first (rank 0 = first). Used to exercise the
    all-or-nothing recall_all vs recall_any distinction directly.
    """
    from memeval.native.spec import PerTaskRecord
    from memeval.schema import (
        Benchmark as _B,
        MemoryItem,
        RetrievedItem,
        Trajectory,
        TrajectoryStep,
    )

    traj = Trajectory(task_id=task_id, benchmark=_B.LONGMEMEVAL, model="t", memory_on=True)
    hits = [
        RetrievedItem(
            item=MemoryItem(item_id=str(sid), content="", timestamp=0.0),
            score=1.0 - 0.01 * i,
            rank=i,
        )
        for i, sid in enumerate(retrieved_ids)
    ]
    traj.add(TrajectoryStep(step=0, kind="retrieve", content="q", retrieved=hits))
    rec = PerTaskRecord.from_trajectory(traj)
    rec.extra["label"] = label
    rec.extra["abstention"] = False
    rec.extra["question_type"] = "multi_session"
    return rec


def test_recall_all_is_all_or_nothing_for_multigold() -> None:
    """recall_all@k = 1.0 ONLY if EVERY gold session is in top-k (not fractional).

    This is the case single-gold fixtures hide: with 2 gold sessions and only 1
    retrieved within top-k, the official recall_all is 0.0 (impl was 0.5);
    recall_any is 1.0.
    """
    from memeval.schema import Benchmark as _B, Task, TaskKind

    # Two gold sessions; the retriever surfaces ONLY one of them at rank 0.
    task = Task(
        task_id="mg_1",
        benchmark=_B.LONGMEMEVAL,
        kind=TaskKind.QA,
        question="q",
        answer="a",
        competency="multi_session",
        gold_memory_ids=["g1", "g2"],
    )
    rec = _multigold_record("mg_1", ["g1", "g2"], retrieved_ids=["g1", "d1", "d2"])

    ev = LongMemEvalNativeEvaluator()
    report = ev.score([rec], [task])

    # Only g1 is in top-3, g2 is missing -> recall_all = 0.0 (NOT 0.5).
    r3 = report.metric("answer_session_recall_at_3")
    assert r3 is not None and r3.value == 0.0, f"recall_all@3 should be 0.0, got {r3.value}"
    # recall_any@3 = 1.0 (at least one gold present).
    a3 = report.metric("answer_session_recall_any_at_3")
    assert a3 is not None and a3.value == 1.0, f"recall_any@3 should be 1.0, got {a3.value}"

    # Now surface BOTH gold within top-3 -> recall_all = 1.0.
    rec2 = _multigold_record("mg_1", ["g1", "g2"], retrieved_ids=["g1", "g2", "d1"])
    report2 = ev.score([rec2], [task])
    assert report2.metric("answer_session_recall_at_3").value == 1.0
    assert report2.metric("answer_session_recall_any_at_3").value == 1.0
    # And at k=1 only g1 is in top-1 -> recall_all@1 = 0.0, recall_any@1 = 1.0.
    assert report2.metric("answer_session_recall_at_1").value == 0.0
    assert report2.metric("answer_session_recall_any_at_1").value == 1.0


def test_lme_ndcg_idiosyncratic_discount() -> None:
    """_lme_ndcg uses LongMemEval's DCG: positions 0 and 1 share discount 1.0."""
    # DCG of [1, 1] = 1 + 1/log2(2) = 1 + 1 = 2.0 (position 1 discount == 1).
    assert _lme_dcg([1.0, 1.0]) == 2.0
    # DCG of [1, 0, 1] = 1 + 0 + 1/log2(3).
    import math as _m
    assert abs(_lme_dcg([1.0, 0.0, 1.0]) - (1.0 + 1.0 / _m.log2(3))) < 1e-12

    # Gold at rank 0 with 1 gold -> perfect NDCG = 1.0.
    assert _lme_ndcg([1.0, 0.0, 0.0], gold_count=1) == 1.0
    # Two gold, both at ranks 0 and 1 -> ideal == actual -> 1.0.
    assert _lme_ndcg([1.0, 1.0, 0.0], gold_count=2) == 1.0
    # Two gold but one at rank 2: actual = 1 + 1/log2(3); ideal = 1 + 1 = 2.
    val = _lme_ndcg([1.0, 0.0, 1.0], gold_count=2)
    assert abs(val - ((1.0 + 1.0 / _m.log2(3)) / 2.0)) < 1e-12
    # No gold -> 0.0.
    assert _lme_ndcg([0.0, 0.0], gold_count=0) == 0.0


def test_shared_store_rejected_to_preserve_independent_trials() -> None:
    """Passing an explicit shared store must raise (it would leak memory)."""
    from memeval.harness import InMemoryStore

    tasks = get_loader(Benchmark.LONGMEMEVAL).load(str(_FIXTURES / "longmemeval.json"))
    ev = LongMemEvalNativeEvaluator()
    shared = InMemoryStore()
    raised = False
    try:
        ev.run(tasks, mode="echo", store=shared, judge=DeterministicJudge())
    except ValueError:
        raised = True
    assert raised, "a shared store must be rejected for the independent-trial protocol"
    # Explicit opt-in is allowed (caller asserts the store resets per task).
    recs = ev.run(
        tasks, mode="echo", store=shared, judge=DeterministicJudge(),
        allow_shared_store=True,
    )
    assert len(recs) == len(tasks)


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
