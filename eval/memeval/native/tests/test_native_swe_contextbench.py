"""Offline tests for the SWE-ContextBench native evaluator.

Fully offline + deterministic: EchoAgent + EchoModel + per-group InMemoryStore +
the dependency-free overlap grader. No network, no Docker, no LLM. Runs both
under pytest and as a standalone script (``python test_native_swe_contextbench.py``).

Asserts the evaluator runs end-to-end and that every native metric + component
slice from the spec is computed and in range:

* headline ``resolve_rate`` / ``context_lift`` / ``overall_matched`` /
  ``efficiency`` / ``avg_tokens`` / ``avg_tool_calls``,
* the subset components (overall / experience_tasks / related_tasks),
* the five paper config components (no_context / free|oracle × context|summary),
* by_language / by_difficulty / retrieval_quality@k / localization_granularity,
* a positive A/B context lift on a crafted fixture where memory carries the answer,
* localization scoring when the prediction IS a real unified diff (reply EchoModel).
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

from memeval.agent import EchoAgent  # noqa: E402
from memeval.models import EchoModel  # noqa: E402
from memeval.native import (  # noqa: E402
    BenchmarkNativeReport,
    DeterministicJudge,
    run_native,
)
from memeval.native.evaluators.swe_contextbench import (  # noqa: E402
    SWEContextBenchNativeEvaluator,
    _diff_locations,
)
from memeval.native.registry import (  # noqa: E402
    get_native_evaluator,
    register_native_evaluator,
)
from memeval.schema import Benchmark, Session, Task, TaskKind  # noqa: E402

_FIXTURE = _EVAL_ROOT / "tests" / "fixtures" / "swe_contextbench.json"


# --------------------------------------------------------------------------- #
# Inline fixtures (tiny, self-contained — used alongside the bundled JSON).
# --------------------------------------------------------------------------- #
def _inline_group() -> list[Task]:
    """A 2-task shared-context group: experience (order 0) + related (order 1).

    The related task's session restates the experience answer so a memory-ON run
    can echo it back, producing a deterministic context lift offline.
    """
    exp = Task(
        task_id="scb_exp_1",
        benchmark=Benchmark.SWE_CONTEXTBENCH,
        kind=TaskKind.CODE,
        question="Fix the crash in widget.render when items is None.",
        patch="diff --git a/widget.py b/widget.py\n"
              "--- a/widget.py\n+++ b/widget.py\n"
              "@@ -10,3 +10,4 @@ def render(items):\n"
              "-    return [w for w in items]\n"
              "+    return [w for w in (items or [])]\n",
        fail_to_pass=["test_widget.py::test_none"],
        pass_to_pass=["test_widget.py::test_basic"],
        group_id="widget_ctx",
        order=0,
        competency="python",
        sessions=[
            Session(
                session_id="scb_exp_1_summary",
                content="In widget_ctx, widget.render was fixed by guarding items "
                        "with (items or []) to stop the None crash.",
                timestamp=0.0,
                index=0,
                role="system",
                metadata={"group_id": "widget_ctx"},
            )
        ],
        metadata={"difficulty": "easy", "language": "python"},
    )
    rel = Task(
        task_id="scb_rel_1",
        benchmark=Benchmark.SWE_CONTEXTBENCH,
        kind=TaskKind.CODE,
        question="Guard widget.render against a None items argument again.",
        patch="diff --git a/widget.py b/widget.py\n"
              "--- a/widget.py\n+++ b/widget.py\n"
              "@@ -10,3 +10,4 @@ def render(items):\n"
              "+    items = items or []\n",
        fail_to_pass=["test_widget.py::test_none2"],
        pass_to_pass=["test_widget.py::test_basic"],
        group_id="widget_ctx",
        order=1,
        competency="python",
        sessions=[
            Session(
                session_id="scb_exp_1_ctx",
                content="Earlier in widget_ctx we guarded items with (items or []) "
                        "in widget.render to fix the None crash.",
                timestamp=0.0,
                index=0,
                role="system",
                metadata={"group_id": "widget_ctx"},
            )
        ],
        metadata={"difficulty": "hard", "language": "python"},
    )
    return [exp, rel]


def _run_eval(tasks: list[Task], *, agent: Any = None) -> tuple[Any, Any, Any]:
    ev = SWEContextBenchNativeEvaluator()
    records = ev.run(tasks, agent_or_model=agent, mode="echo",
                     judge=DeterministicJudge(), k=3)
    report = ev.score(records, tasks)
    return ev, records, report


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_benchmark_id_and_protocol() -> None:
    ev = SWEContextBenchNativeEvaluator()
    assert ev.benchmark == Benchmark.SWE_CONTEXTBENCH.value == "swe_contextbench"
    assert hasattr(ev, "run") and hasattr(ev, "score")


def test_runs_end_to_end_offline() -> None:
    _ev, records, report = _run_eval(_inline_group())
    # Two passes (ON + OFF) over two tasks -> 4 records.
    assert len(records) == 4
    assert {r.memory_on for r in records} == {True, False}
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "swe_contextbench"
    assert report.n_tasks == 2
    # Round-trips to JSON (the CLI dumps this).
    json.dumps(report.to_dict())


def test_headline_metrics_present_and_in_range() -> None:
    _ev, _records, report = _run_eval(_inline_group())
    for name in ("resolve_rate", "context_lift", "overall_matched",
                 "efficiency", "avg_tokens", "avg_tool_calls"):
        m = report.metric(name)
        assert m is not None, f"missing headline metric {name}"
    # Bounded metrics in [0,1]; lift in [-1,1].
    assert 0.0 <= report.metric("resolve_rate").value <= 1.0
    assert -1.0 <= report.metric("context_lift").value <= 1.0
    assert 0.0 <= report.metric("overall_matched").value <= 1.0
    assert report.metric("efficiency").value >= 0.0
    assert report.metric("efficiency").better == "lower"
    assert report.metric("avg_tokens").value >= 0.0


def test_all_spec_components_present() -> None:
    _ev, _records, report = _run_eval(_inline_group())
    expected = {
        "overall", "experience_tasks", "related_tasks",
        "config:no_context", "config:free_context", "config:oracle_context",
        "config:free_summary", "config:oracle_summary",
        "by_language", "by_difficulty",
        "retrieval_quality@k", "localization_granularity",
    }
    assert expected <= set(report.components), (
        f"missing: {expected - set(report.components)}"
    )


def test_retrieval_quality_k_metrics() -> None:
    _ev, _records, report = _run_eval(_inline_group())
    rq = report.components["retrieval_quality@k"]
    for k in (1, 2, 3):
        mr = rq.get(f"match_rate@{k}")
        cr = rq.get(f"context_recall@{k}")
        assert mr is not None and 0.0 <= mr.value <= 1.0
        assert cr is not None and 0.0 <= cr.value <= 1.0
    assert rq.get("overall_matched") is not None


def test_context_lift_positive_when_memory_carries_answer() -> None:
    """Memory-ON should retrieve the sibling context and resolve >= memory-OFF.

    The related task's prediction under memory-ON echoes the seeded sibling
    content (which restates the gold change), so the overlap grader vs the gold
    patch resolves more often with memory than without -> non-negative lift.
    """
    _ev, _records, report = _run_eval(_inline_group())
    rel = report.components["related_tasks"]
    rr_on = rel.get("resolve_rate").value
    rr_off = rel.get("resolve_rate_memory_off").value
    lift = rel.get("context_lift").value
    assert abs(lift - (rr_on - rr_off)) < 1e-9
    assert lift >= 0.0  # context must not hurt on this crafted group


def test_match_rate_fires_on_sibling_retrieval() -> None:
    """The related task must retrieve the experience sibling's context id."""
    ev = SWEContextBenchNativeEvaluator()
    tasks = _inline_group()
    records = ev.run(tasks, mode="echo", k=3)
    gold = ev._gold_sibling_ids(tasks)
    # The related task's gold set = the experience SIBLING's session id (its
    # context-pool summary) + the sibling's EchoAgent write-back id.
    assert "scb_exp_1_summary" in gold["scb_rel_1"]
    assert "scb_exp_1::mem0" in gold["scb_rel_1"]
    report = ev.score(records, tasks)
    # With one prior sibling whose context is retrievable, match_rate@3 > 0.
    rq = report.components["retrieval_quality@k"]
    assert rq.get("match_rate@3").value > 0.0


def test_localization_with_real_diff_prediction() -> None:
    """When the agent returns a real unified diff, localization scores it.

    A reply-fixed EchoModel forces every prediction to BE the gold-style diff, so
    file/function/line correct-location all fire (n>0, file=1.0).
    """
    gold_diff = (
        "diff --git a/widget.py b/widget.py\n"
        "--- a/widget.py\n+++ b/widget.py\n"
        "@@ -10,3 +10,4 @@ def render(items):\n"
        "-    return [w for w in items]\n"
        "+    return [w for w in (items or [])]\n"
    )
    agent = EchoAgent(model=EchoModel(reply=gold_diff))
    tasks = _inline_group()
    _ev, _records, report = _run_eval(tasks, agent=agent)
    loc = report.components["localization_granularity"]
    assert loc.n > 0
    fa = loc.get("file_correct_location")
    fn = loc.get("function_correct_location")
    ln = loc.get("line_correct_location")
    assert fa is not None and fa.value == 1.0  # gold file fully covered
    assert fn is not None and 0.0 <= fn.value <= 1.0
    assert ln is not None and ln.value > 0.0


def test_diff_location_parser() -> None:
    """Unit-test the pure diff parser used by localization."""
    diff = (
        "diff --git a/pkg/mod.py b/pkg/mod.py\n"
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
        "@@ -5,2 +5,3 @@ def handle(req):\n"
        "+    log(req)\n"
    )
    loc = _diff_locations(diff)
    assert "pkg/mod.py" in loc["files"]
    assert ("pkg/mod.py", "handle") in loc["funcs"]
    assert ("pkg/mod.py", 5) in loc["lines"]
    # Non-diff text yields nothing (offline echo path), no crash.
    empty = _diff_locations("just an answer, not a diff")
    assert empty["files"] == set()


def test_experience_vs_related_split() -> None:
    _ev, _records, report = _run_eval(_inline_group())
    exp = report.components["experience_tasks"]
    rel = report.components["related_tasks"]
    assert exp.n == 1 and rel.n == 1
    assert report.metadata["n_experience"] == 1
    assert report.metadata["n_related"] == 1


def test_runs_over_bundled_fixture_via_run_native() -> None:
    """Full runner path over the committed fixture (loader + evaluator)."""
    assert _FIXTURE.exists(), f"missing fixture {_FIXTURE}"
    register_native_evaluator(
        Benchmark.SWE_CONTEXTBENCH, SWEContextBenchNativeEvaluator()
    )
    report = run_native(
        Benchmark.SWE_CONTEXTBENCH,
        model_or_agent=None,
        mode="echo",
        path_or_id=str(_FIXTURE),
        limit=10,
    )
    assert isinstance(report, BenchmarkNativeReport)
    assert report.benchmark == "swe_contextbench"
    assert report.n_tasks >= 1
    # headline metrics survive the loader path
    assert report.metric("resolve_rate") is not None
    assert report.metric("context_lift") is not None
    # provenance stamped by the runner
    assert report.metadata.get("source") == str(_FIXTURE)
    json.dumps(report.to_dict())


def test_registry_resolves_class() -> None:
    """The pre-mapped registry resolves our class without manual registration."""
    # Clear any eager override so the lazy module map is exercised.
    from memeval.native import registry as _reg
    _reg._OVERRIDES.pop(Benchmark.SWE_CONTEXTBENCH, None)
    ev = get_native_evaluator(Benchmark.SWE_CONTEXTBENCH)
    assert isinstance(ev, SWEContextBenchNativeEvaluator)


def test_score_is_deterministic() -> None:
    """score() is pure: same records+tasks -> identical report dict."""
    ev = SWEContextBenchNativeEvaluator()
    tasks = _inline_group()
    records = ev.run(tasks, mode="echo", k=3)
    d1 = ev.score(records, tasks).to_dict()
    d2 = ev.score(records, tasks).to_dict()
    assert d1 == d2


# --------------------------------------------------------------------------- #
# Standalone runner
# --------------------------------------------------------------------------- #
def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
