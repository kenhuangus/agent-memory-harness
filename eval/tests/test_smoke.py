"""Offline smoke tests for the memeval evaluation harness.

These tests exercise the WHOLE offline path with the standard library only --
no network, no ``datasets``/``anthropic``/``numpy``/``pyyaml``/``requests``.
They are the green-light gate for the parallel build: every workstream's
offline contribution (schema, loaders, metrics, cost, trajectory IO, models,
harness) is poked here against the frozen contract in ``memeval.schema`` /
``memeval.protocols``.

Two ways to run, both supported (no pytest install required):

    python -m pytest tests
    python tests/test_smoke.py        # built-in runner; prints PASS/FAIL/SKIP

What is covered
---------------
* Each benchmark loader parses its tiny hand-written fixture into ``Task``s
  with the expected shape (kind, gold ids, sessions, group/order).
* Each of the four metrics returns the exact value computed on a *crafted*
  trajectory whose inputs we fully control (independent of the harness).
* QA grading helpers (``normalize_answer`` / ``qa_match``) and ``cosine``.
* ``CostTracker`` raises ``BudgetExceeded`` on a USD and a token overrun, and
  never raises with no budget.
* ``TrajectoryLogger`` writes JSONL that ``read_trajectory_list`` round-trips
  losslessly (including nested ``RetrievedItem`` -> ``MemoryItem`` + embedding),
  and the provided ``trajectory.jsonl`` fixture loads.
* ``EchoModel`` is deterministic and memory-sensitive.
* ``harness.run(<benchmark>, EchoModel, memory=False/True)`` returns a
  ``RunResult`` with all four metrics populated, for every benchmark.

In-flight modules (``memeval.harness`` and the SWE-Bench-CL loader) may not be
on disk yet during the parallel build; tests that need them raise
``unittest.SkipTest`` when the module is absent, so the suite stays green and
auto-upgrades to a full run the moment those siblings land.
"""

from __future__ import annotations

import importlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Make the package importable when run as ``python tests/test_smoke.py`` from
# anywhere: the eval base dir (parent of this file's parent) holds ``memeval``.
# --------------------------------------------------------------------------- #
_THIS = Path(__file__).resolve()
_TESTS_DIR = _THIS.parent
_BASE_DIR = _TESTS_DIR.parent
_FIXTURES = _TESTS_DIR / "fixtures"

import sys

if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# Frozen-contract imports (stdlib-only modules; always present).
from memeval.schema import (  # noqa: E402
    Benchmark,
    MemoryItem,
    Metrics,
    ModelConfig,
    RetrievedItem,
    RunResult,
    Task,
    TaskKind,
    Trajectory,
    TrajectoryStep,
)
from memeval import metrics as M  # noqa: E402
from memeval import cost as C  # noqa: E402
from memeval.cost import BudgetExceeded, CostTracker  # noqa: E402
from memeval.models import EchoModel, estimate_tokens  # noqa: E402
from memeval.trajectory import (  # noqa: E402
    TrajectoryLogger,
    read_trajectory_list,
    trajectory_from_dict,
    trajectory_to_dict,
)
from memeval.loaders import available, get_loader  # noqa: E402

# Use unittest.SkipTest so pytest treats it as a skip AND the __main__ runner
# can distinguish skip from failure.
SkipTest = unittest.SkipTest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fixture(name: str) -> str:
    """Absolute path to a fixture file (resolves from this test's directory)."""
    p = _FIXTURES / name
    if not p.is_file():
        raise AssertionError(f"missing fixture: {p}")
    return str(p)


def _try_import(module: str):
    """Import ``module`` or raise SkipTest if it is not on disk yet.

    Lets the suite stay green while sibling workstreams (harness, swe_bench_cl)
    are still being written, then auto-runs the full path once they land.
    """
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        # Only skip when it's THIS module that's missing -- a missing transitive
        # import is a real failure we want to surface.
        if exc.name == module or (exc.name and module.startswith(exc.name)):
            raise SkipTest(f"{module} not on disk yet ({exc})") from exc
        raise


def _mk_item(item_id: str, ts: float, tokens: int, *, score: float = 1.0,
             embedding: Optional[list[float]] = None) -> MemoryItem:
    return MemoryItem(
        item_id=item_id, content=item_id, timestamp=ts, tokens=tokens,
        embedding=embedding,
    )


def _crafted_trajectory() -> tuple[list[Trajectory], list[Task]]:
    """Build one fully-controlled (trajectory, task) pair for metric math.

    Layout: gold item ``g1`` (ts=1000) retrieved at rank 0; non-gold ``n1``
    (ts=990) at rank 1; the retrieve step's query time is exactly 1 day after
    g1's timestamp (so the decayed recency is exp(-1)). The generate step bills
    100 in + 20 out tokens; memory tokens = 10 + 5 = 15.

    Expected metrics:
        recency         = 1.0          (freshest gold ranked #1)
        recency_decayed = exp(-1)      (dt == tau == 86400s)
        efficiency      = 15 / 120     (memory_tokens / total_tokens)
        relevancy       = (0.9+0.5)/2  = 0.7   (mean score)
        precision@k     = 1/2          = 0.5   (only 0.9 >= 0.7)
        accuracy        = 1.0          (success is True)
    """
    task = Task(
        task_id="t1", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
        question="capital of France?", answer="paris",
        gold_memory_ids=["g1"],
    )
    traj = Trajectory(
        task_id="t1", benchmark=Benchmark.LONGMEMEVAL, model="echo",
        memory_on=True, prediction="paris", success=True,
    )
    retrieved = [
        RetrievedItem(item=_mk_item("g1", 1000.0, 10), score=0.9, rank=0),
        RetrievedItem(item=_mk_item("n1", 990.0, 5), score=0.5, rank=1),
    ]
    traj.add(TrajectoryStep(
        step=0, kind="retrieve", timestamp=1000.0 + M.TAU_DEFAULT,
        retrieved=retrieved,
    ))
    traj.add(TrajectoryStep(
        step=1, kind="generate", tokens_in=100, tokens_out=20, content="paris",
    ))
    return [traj], [task]


def _approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Loaders -- each fixture parses into the expected Tasks
# --------------------------------------------------------------------------- #
def test_memoryagentbench_loader_parses_fixture() -> None:
    tasks = get_loader("memoryagentbench").load(_fixture("memoryagentbench.json"))
    assert len(tasks) == 2, f"expected 2 tasks, got {len(tasks)}"
    t0 = tasks[0]
    assert t0.benchmark is Benchmark.MEMORY_AGENT_BENCH
    assert t0.kind is TaskKind.QA
    assert t0.task_id == "mab_event_1"
    assert t0.answer == "Berlin"
    assert t0.gold_memory_ids == ["mab_s_move"]
    assert len(t0.sessions) == 2
    # Sessions carry parsed epoch timestamps used by the recency metric.
    assert all(s.timestamp > 0 for s in t0.sessions)
    # The gold session id must exist among the parsed sessions.
    sess_ids = {s.session_id for s in t0.sessions}
    assert "mab_s_move" in sess_ids
    # Competency canonicalized (EventQA -> accurate_retrieval).
    assert t0.competency == "accurate_retrieval"


def test_longmemeval_loader_parses_fixture() -> None:
    tasks = get_loader("longmemeval").load(_fixture("longmemeval.json"))
    assert len(tasks) == 2
    t0 = tasks[0]
    assert t0.benchmark is Benchmark.LONGMEMEVAL
    assert t0.kind is TaskKind.QA
    assert t0.task_id == "lme_temporal_1"
    assert t0.answer == "Lisbon"
    assert t0.competency == "temporal_reasoning"
    # haystack arrays reconstruct sessions with parallel ids/timestamps.
    assert len(t0.sessions) == 2
    assert t0.sessions[0].session_id == "lme_sess_a"
    assert t0.sessions[1].session_id == "lme_sess_b"
    assert t0.sessions[1].timestamp > t0.sessions[0].timestamp
    assert t0.gold_memory_ids == ["lme_sess_b"]
    # Abstention row: id suffix _abs flags it in metadata; gold ids empty.
    abst = tasks[1]
    assert abst.task_id.endswith("_abs")
    assert abst.gold_memory_ids == []
    assert abst.metadata.get("abstention") is True


def test_swe_contextbench_loader_parses_fixture() -> None:
    tasks = get_loader("swe_contextbench").load(_fixture("swe_contextbench.json"))
    assert len(tasks) == 2
    t0, t1 = tasks
    assert t0.benchmark is Benchmark.SWE_CONTEXTBENCH
    assert t0.kind is TaskKind.CODE
    assert t0.repo == "example/django-fork"
    assert t0.base_commit == "aaaa1111"
    assert t0.patch and t0.patch.startswith("diff --git")
    assert t0.fail_to_pass == ["test_orm.py::test_empty"]
    assert "test_orm.py::test_basic" in t0.pass_to_pass
    # Shared-context grouping + within-group ordering.
    assert t0.group_id == "django_orm_ctx"
    assert t1.group_id == "django_orm_ctx"
    assert t0.order == 0 and t1.order == 1
    # CODE tasks have no QA answer.
    assert t0.answer is None
    # Context blob is exposed as a retrievable session.
    assert len(t0.sessions) >= 1


def test_swe_bench_cl_loader_parses_fixture() -> None:
    # Loader may not be on disk yet during the parallel build.
    _try_import("memeval.loaders.swe_bench_cl")
    tasks = get_loader("swe_bench_cl").load(_fixture("swe_bench_cl.json"))
    assert len(tasks) == 2
    t0, t1 = tasks
    assert t0.benchmark is Benchmark.SWE_BENCH_CL
    assert t0.kind is TaskKind.CODE
    assert t0.repo == "astropy/astropy"
    assert t0.patch and t0.patch.startswith("diff --git")
    assert t0.fail_to_pass == ["test_units.py::test_dimensionless"]
    # group_id == sequence; chronological within-sequence order preserved.
    assert t0.group_id == t1.group_id
    assert t0.order == 0 and t1.order == 1


def test_loader_registry_lists_all_four() -> None:
    benches = set(available())
    assert benches == {
        Benchmark.MEMORY_AGENT_BENCH,
        Benchmark.LONGMEMEVAL,
        Benchmark.SWE_CONTEXTBENCH,
        Benchmark.SWE_BENCH_CL,
    }
    # Loose-string resolution goes through Benchmark.from_str.
    assert get_loader("LongMemEval").benchmark is Benchmark.LONGMEMEVAL
    assert get_loader("swe-bench-cl").benchmark is Benchmark.SWE_BENCH_CL


# --------------------------------------------------------------------------- #
# Metrics -- exact values on a crafted trajectory
# --------------------------------------------------------------------------- #
def test_recency_metric() -> None:
    trajs, tasks = _crafted_trajectory()
    rec, rec_dec = M.recency(trajs, tasks)
    assert _approx(rec, 1.0), rec
    assert _approx(rec_dec, math.exp(-1.0)), rec_dec
    # Side effect of recency: is_gold gets annotated on the retrieved items.
    flags = {r.item_id: r.is_gold for r in trajs[0].steps[0].retrieved}
    assert flags == {"g1": True, "n1": False}


def test_recency_not_ranked_first() -> None:
    # Same setup but the freshest gold is at rank 1 -> recency == 0.
    task = Task(task_id="t", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
                question="q", gold_memory_ids=["g"])
    traj = Trajectory(task_id="t", benchmark=Benchmark.LONGMEMEVAL, model="echo")
    traj.add(TrajectoryStep(step=0, kind="retrieve", timestamp=100.0, retrieved=[
        RetrievedItem(item=_mk_item("other", 50.0, 1), score=0.4, rank=0),
        RetrievedItem(item=_mk_item("g", 60.0, 1), score=0.3, rank=1),
    ]))
    rec, _ = M.recency([traj], [task])
    assert _approx(rec, 0.0), rec


def test_efficiency_metric() -> None:
    trajs, _ = _crafted_trajectory()
    eff = M.efficiency(trajs)
    assert _approx(eff, 15.0 / 120.0), eff
    # No-token trajectory contributes nothing (returns 0.0, not a crash).
    empty = Trajectory(task_id="e", benchmark=Benchmark.LONGMEMEVAL, model="echo")
    assert _approx(M.efficiency([empty]), 0.0)


def test_relevancy_metric() -> None:
    trajs, tasks = _crafted_trajectory()
    rel, prec = M.relevancy(trajs, tasks)
    assert _approx(rel, (0.9 + 0.5) / 2.0), rel
    assert _approx(prec, 0.5), prec  # only 0.9 >= 0.7 threshold


def test_relevancy_with_embeddings() -> None:
    # When query embeddings are supplied and items carry embeddings, the metric
    # uses cosine(query, item) instead of the stored score.
    task = Task(task_id="t", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
                question="q", gold_memory_ids=["g"])
    traj = Trajectory(task_id="t", benchmark=Benchmark.LONGMEMEVAL, model="echo")
    traj.add(TrajectoryStep(step=0, kind="retrieve", timestamp=10.0, retrieved=[
        RetrievedItem(item=_mk_item("g", 5.0, 1, embedding=[1.0, 0.0]),
                      score=0.0, rank=0),
    ]))
    rel, prec = M.relevancy([traj], [task],
                            query_embeddings={"t": [1.0, 0.0]})
    assert _approx(rel, 1.0), rel  # identical unit vectors -> cosine 1.0
    assert _approx(prec, 1.0), prec


def test_accuracy_metric() -> None:
    trajs, _ = _crafted_trajectory()
    assert _approx(M.accuracy(trajs), 1.0)
    # Mixed: one success, one failure, one ungraded (ignored).
    t_ok = Trajectory(task_id="a", benchmark=Benchmark.LONGMEMEVAL, model="x",
                      success=True)
    t_no = Trajectory(task_id="b", benchmark=Benchmark.LONGMEMEVAL, model="x",
                      success=False)
    t_none = Trajectory(task_id="c", benchmark=Benchmark.LONGMEMEVAL, model="x",
                        success=None)
    assert _approx(M.accuracy([t_ok, t_no, t_none]), 0.5)
    # No graded trajectories -> 0.0.
    assert _approx(M.accuracy([t_none]), 0.0)


def test_qa_grading_helpers() -> None:
    assert M.normalize_answer("The  Eiffel-Tower!") == "eiffel tower"
    assert M.normalize_answer(None) == ""  # type: ignore[arg-type]
    assert M.qa_match("Paris", "paris") is True
    assert M.qa_match("The capital is Paris.", "Paris") is True  # substring
    assert M.qa_match("London", "Paris") is False


def test_cosine() -> None:
    assert _approx(M.cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
    assert _approx(M.cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
    assert _approx(M.cosine([], [1.0]), 0.0)         # empty
    assert _approx(M.cosine([0.0, 0.0], [1.0, 1.0]), 0.0)  # zero vector


def test_compute_metrics_aggregate() -> None:
    trajs, tasks = _crafted_trajectory()
    m = M.compute_metrics(trajs, tasks, accuracy_memory_off=0.0)
    assert isinstance(m, Metrics)
    assert _approx(m.recency, 1.0)
    assert _approx(m.recency_decayed, math.exp(-1.0))
    assert _approx(m.efficiency, 15.0 / 120.0)
    assert _approx(m.relevancy, 0.7)
    assert _approx(m.precision_at_k, 0.5)
    assert _approx(m.accuracy, 1.0)
    assert m.n == 1
    # accuracy_lift = memory-on - memory-off.
    assert m.accuracy_lift is not None and _approx(m.accuracy_lift, 1.0)
    # to_dict is JSON-serializable.
    json.dumps(m.to_dict())


# --------------------------------------------------------------------------- #
# Cost -- budget enforcement
# --------------------------------------------------------------------------- #
def test_cost_of_and_pricing() -> None:
    # Prices are USD per MILLION tokens (invariant #2).
    assert _approx(C.cost_of("claude-haiku-4-5", 1_000_000, 1_000_000), 6.0)
    assert _approx(C.cost_of("echo", 1_000_000, 1_000_000), 0.0)
    # Unknown model falls back to zero, never KeyErrors.
    assert _approx(C.cost_of("mystery-model", 1_000_000, 0), 0.0)


def test_cost_tracker_usd_budget_raises() -> None:
    ct = CostTracker(budget_usd=0.001)
    raised = False
    try:
        ct.add("claude-opus-4-8", 1_000_000, 0)  # $15 >> $0.001
    except BudgetExceeded as exc:
        raised = True
        # Totals reflect the offending call (harness emits a partial result).
        assert exc.spent_usd > 0.001
        assert exc.budget_usd == 0.001
        assert exc.tokens == 1_000_000
    assert raised, "expected BudgetExceeded on USD overrun"
    assert ct.spent_usd > 0.001


def test_cost_tracker_token_budget_raises() -> None:
    ct = CostTracker(budget_tokens=10)
    raised = False
    try:
        ct.add("echo", 8, 5)  # 13 tokens > 10
    except BudgetExceeded as exc:
        raised = True
        assert exc.tokens == 13
    assert raised, "expected BudgetExceeded on token overrun"


def test_cost_tracker_no_budget_never_raises() -> None:
    ct = CostTracker()
    ct.add("claude-opus-4-8", 5_000_000, 5_000_000)
    assert ct.spent_usd > 0
    assert ct.would_exceed("claude-opus-4-8", 1, 1) is False
    snap = ct.snapshot()
    json.dumps(snap)  # serializable
    assert snap["budget_usd"] is None


def test_load_key_config() -> None:
    cfg = C.load_key_config(_BASE_DIR / "memeval" / "config" / "keys.example.json")
    assert set(cfg.keys()) == {
        "swe_bench_cl", "longmemeval", "swe_contextbench", "memoryagentbench",
    }
    assert cfg["longmemeval"]["captain"] == "Ken"
    assert cfg["longmemeval"]["api_key_env"] == "ANTHROPIC_API_KEY_KEN"
    assert cfg["swe_bench_cl"]["captain"] == "Keith"
    # Comment keys (leading _) are stripped.
    assert all(not k.startswith("_") for k in cfg)


# --------------------------------------------------------------------------- #
# Trajectory -- JSONL round-trip
# --------------------------------------------------------------------------- #
def _sample_trajectory() -> Trajectory:
    traj = Trajectory(
        task_id="rt1", benchmark=Benchmark.LONGMEMEVAL, model="haiku+mem",
        memory_on=True, prediction="Paris", success=True,
        started_at=1.0, ended_at=2.0, metadata={"competency": "temporal"},
    )
    traj.add(TrajectoryStep(
        step=0, kind="retrieve", timestamp=1.5,
        retrieved=[RetrievedItem(
            item=MemoryItem(item_id="m1", content="Paris is the capital.",
                            timestamp=0.5, tokens=5, embedding=[0.1, 0.2],
                            tags=["geo"]),
            score=0.92, rank=0, is_gold=True,
        )],
    ))
    traj.add(TrajectoryStep(
        step=1, kind="generate", tokens_in=10, tokens_out=2, content="Paris",
    ))
    return traj


def test_trajectory_dict_round_trip() -> None:
    traj = _sample_trajectory()
    back = trajectory_from_dict(trajectory_to_dict(traj))
    assert back.task_id == traj.task_id
    assert back.benchmark is Benchmark.LONGMEMEVAL
    assert back.memory_on is True
    assert back.success is True
    assert len(back.steps) == 2
    assert back.memory_tokens == 5
    assert back.total_tokens == 12
    ri = back.steps[0].retrieved[0]
    assert ri.is_gold is True
    assert ri.item.embedding == [0.1, 0.2]
    assert ri.item.tags == ["geo"]


def test_trajectory_logger_jsonl_round_trip() -> None:
    traj = _sample_trajectory()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.jsonl"
        with TrajectoryLogger(path, append=False) as log:
            log.log(traj)
            log.log(traj)
        # File is valid JSONL: one object per non-empty line.
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 2
        for ln in lines:
            json.loads(ln)  # each line parses
        read = read_trajectory_list(path)
    assert len(read) == 2
    assert read[0].prediction == "Paris"
    assert read[0].success is True
    assert read[0].steps[0].retrieved[0].is_gold is True


def test_trajectory_fixture_loads() -> None:
    read = read_trajectory_list(_fixture("trajectory.jsonl"))
    assert len(read) == 3
    by_id = {t.task_id: t for t in read}
    assert "lme_q1" in by_id and "swecl_q1" in by_id
    assert by_id["lme_q1"].benchmark is Benchmark.LONGMEMEVAL
    assert by_id["swecl_q1"].benchmark is Benchmark.SWE_BENCH_CL
    # Ungraded trajectory keeps success == None.
    assert by_id["swecl_q1"].success is None
    # Metrics can be computed over fixture trajectories without crashing.
    tasks = [
        Task(task_id="lme_q1", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
             question="capital of France?", answer="Paris",
             gold_memory_ids=["s3"]),
    ]
    m = M.compute_metrics(read, tasks)
    assert isinstance(m, Metrics)
    assert _approx(m.recency, 1.0)  # s3 is gold, freshest, ranked #1


# --------------------------------------------------------------------------- #
# Models -- EchoModel determinism + memory sensitivity
# --------------------------------------------------------------------------- #
def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 1          # floored at 1
    assert estimate_tokens("abcd") == 1      # 4 chars // 4
    assert estimate_tokens("a" * 40) == 10


def test_echo_model_deterministic_and_memory_sensitive() -> None:
    m = EchoModel()
    prompt = "Question: Which city did the user move to?"
    out1 = m.generate(prompt)
    out2 = m.generate(prompt)
    assert out1 == out2, "EchoModel must be deterministic at temperature 0"
    text, t_in, t_out = out1
    assert isinstance(text, str) and t_in > 0 and t_out >= 1
    # With a retrieved-memory line carrying the answer, EchoModel surfaces it.
    mem_prompt = prompt + "\n[memory] The user relocated to Berlin for work."
    mtext, _, _ = m.generate(mem_prompt)
    assert "Berlin" in mtext
    # An explicit Answer: line takes priority.
    ans_text, _, _ = m.generate("Question: foo\nAnswer: Rust")
    assert ans_text == "Rust"
    # A fixed reply overrides extraction.
    fixed = EchoModel(reply="fixed-answer")
    assert fixed.generate("anything")[0] == "fixed-answer"


def test_echo_model_satisfies_protocol() -> None:
    from memeval.protocols import ModelAdapter
    assert isinstance(EchoModel(), ModelAdapter)


# --------------------------------------------------------------------------- #
# Harness -- run() per benchmark, memory off and on
# --------------------------------------------------------------------------- #
_BENCH_FIXTURES = {
    Benchmark.MEMORY_AGENT_BENCH: "memoryagentbench.json",
    Benchmark.LONGMEMEVAL: "longmemeval.json",
    Benchmark.SWE_CONTEXTBENCH: "swe_contextbench.json",
    Benchmark.SWE_BENCH_CL: "swe_bench_cl.json",
}


def _assert_runresult(rr: object, benchmark: Benchmark, memory: bool) -> None:
    """Assert the object is a contract-shaped RunResult with metrics populated."""
    assert isinstance(rr, RunResult), f"run() must return RunResult, got {type(rr)}"
    assert rr.benchmark is benchmark
    assert isinstance(rr.config, ModelConfig)
    assert rr.config.memory is memory
    assert rr.n_tasks >= 1, "run() should have processed the fixture tasks"
    assert len(rr.trajectories) == rr.n_tasks
    m = rr.metrics
    assert isinstance(m, Metrics)
    # All four metrics are present and numeric (the core deliverable check).
    for field_name in ("recency", "efficiency", "relevancy", "accuracy"):
        val = getattr(m, field_name)
        assert isinstance(val, float), f"{field_name} not a float: {val!r}"
        assert not math.isnan(val), f"{field_name} is NaN"
    assert m.n == rr.n_tasks
    # Cost is tracked (echo is free, so exactly 0.0).
    assert rr.cost_usd == 0.0
    # to_dict is JSON-serializable for the dashboard.
    json.dumps(rr.to_dict())


def _run_one(benchmark: Benchmark, memory: bool) -> None:
    harness = _try_import("memeval.harness")
    fixture = _fixture(_BENCH_FIXTURES[benchmark])
    rr = harness.run(benchmark, EchoModel(), memory, path_or_id=fixture)
    _assert_runresult(rr, benchmark, memory)


def test_harness_run_memoryagentbench_memory_off() -> None:
    _run_one(Benchmark.MEMORY_AGENT_BENCH, False)


def test_harness_run_memoryagentbench_memory_on() -> None:
    _run_one(Benchmark.MEMORY_AGENT_BENCH, True)


def test_harness_run_longmemeval_memory_off() -> None:
    _run_one(Benchmark.LONGMEMEVAL, False)


def test_harness_run_longmemeval_memory_on() -> None:
    _run_one(Benchmark.LONGMEMEVAL, True)


def test_harness_run_swe_contextbench_memory_off() -> None:
    _run_one(Benchmark.SWE_CONTEXTBENCH, False)


def test_harness_run_swe_contextbench_memory_on() -> None:
    _run_one(Benchmark.SWE_CONTEXTBENCH, True)


def test_harness_run_swe_bench_cl_memory_off() -> None:
    _try_import("memeval.loaders.swe_bench_cl")
    _run_one(Benchmark.SWE_BENCH_CL, False)


def test_harness_run_swe_bench_cl_memory_on() -> None:
    _try_import("memeval.loaders.swe_bench_cl")
    _run_one(Benchmark.SWE_BENCH_CL, True)


def test_harness_inmemory_store_satisfies_protocol() -> None:
    harness = _try_import("memeval.harness")
    from memeval.protocols import MemoryStore
    store = harness.InMemoryStore()
    assert isinstance(store, MemoryStore)
    # write/get/search/all behave per the contract: search sets rank + tokens.
    store.write(MemoryItem(item_id="m1", content="Paris is the capital of France",
                           timestamp=10.0, tokens=8))
    store.write(MemoryItem(item_id="m2", content="The weather is sunny today",
                           timestamp=20.0, tokens=6))
    assert store.get("m1") is not None
    assert len(store.all()) == 2
    hits = store.search("capital of France", k=2)
    assert hits, "search should return at least one hit for an overlapping query"
    assert hits[0].rank == 0
    # tokens flow through RetrievedItem (invariant #1) for the efficiency metric.
    assert hits[0].tokens == hits[0].item.tokens


def test_harness_run_with_budget_partial() -> None:
    """A tiny USD budget on a non-free model yields a partial RunResult.

    Uses a fixed-reply EchoModel but a CostTracker priced as opus via an
    injected pricing table, so the first generate call trips the budget and the
    harness must record a partial result rather than raising out.
    """
    harness = _try_import("memeval.harness")
    fixture = _fixture(_BENCH_FIXTURES[Benchmark.LONGMEMEVAL])
    # Price echo like opus so any generation overruns a sub-cent budget.
    pricing = {"echo": {"in": 15.0, "out": 75.0}}
    tracker = CostTracker(budget_usd=1e-9, pricing=pricing)
    rr = harness.run(Benchmark.LONGMEMEVAL, EchoModel(), False,
                     path_or_id=fixture, cost=tracker)
    assert isinstance(rr, RunResult)
    assert rr.budget_exceeded is True
    assert rr.partial is True


# --------------------------------------------------------------------------- #
# Built-in runner (no pytest required)
# --------------------------------------------------------------------------- #
def _all_tests() -> list:
    """Collect every module-level ``test_*`` callable, in definition order."""
    g = globals()
    names = [n for n in g if n.startswith("test_") and callable(g[n])]
    # Preserve definition order via the function's code line number.
    names.sort(key=lambda n: g[n].__code__.co_firstlineno)
    return [(n, g[n]) for n in names]


def main() -> int:
    """Run all tests, print PASS/FAIL/SKIP, return nonzero on any failure."""
    passed = failed = skipped = 0
    failures: list[tuple[str, str]] = []
    for name, fn in _all_tests():
        try:
            fn()
        except SkipTest as exc:
            skipped += 1
            print(f"SKIP {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report every failure
            failed += 1
            import traceback
            tb = traceback.format_exc()
            failures.append((name, tb))
            print(f"FAIL {name}: {exc.__class__.__name__}: {exc}")
        else:
            passed += 1
            print(f"PASS {name}")

    print("-" * 60)
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    if failures:
        print("=" * 60)
        for name, tb in failures:
            print(f"--- {name} ---")
            print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
