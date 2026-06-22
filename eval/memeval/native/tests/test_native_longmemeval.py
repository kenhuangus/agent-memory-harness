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
        assert comp.n == 1  # exactly one question of each type in the fixture

    # Abstention component present and separate from the type buckets.
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
    # The abstention item must NOT appear in any answerable type component.
    for name in _TYPE_COMPONENTS:
        comp = report.components.get(name)
        assert comp is None or comp.n == 0


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
    # temporal item -> qa; abstention item -> abstention
    temporal = next(t for t in tasks if t.competency == "temporal_reasoning")
    assert _judge_kind(temporal) == "qa"
    abst = next(t for t in tasks if t.metadata.get("abstention"))
    assert _judge_kind(abst) == "abstention"

    # _ranked_session_ids reads the last retrieve step in rank order.
    ev = LongMemEvalNativeEvaluator()
    recs = ev.run(tasks, mode="echo", judge=DeterministicJudge())
    rec = next(r for r in recs if r.task_id == temporal.task_id)
    ranked = _ranked_session_ids(rec)
    assert ranked, "memory-on retrieve step should surface session ids"
    # Every id must be one of the task's session ids (item_id == session_id).
    sess_ids = {s.session_id for s in by_id[temporal.task_id].sessions}
    assert set(ranked) <= sess_ids


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
