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
from memeval.agent import (  # noqa: E402
    AgentResult,
    EchoAgent,
    function_agent,
    run_agent,
)
from memeval.harness import InMemoryStore  # noqa: E402
from memeval import aggregate as AGG  # noqa: E402

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


def test_contextbench_loader_parses_fixture() -> None:
    tasks = get_loader("contextbench").load(_fixture("contextbench.json"))
    assert len(tasks) == 2
    t0, t1 = tasks
    assert t0.benchmark is Benchmark.CONTEXTBENCH
    assert t0.kind is TaskKind.CODE
    assert t0.repo == "astropy/astropy"
    assert t0.group_id == "astropy/astropy"
    assert t0.competency == "python"
    assert t0.fail_to_pass == ["test_quantity.py::test_div"]
    assert "test_quantity.py::test_basic" in t0.pass_to_pass
    assert t0.answer is None  # CODE task, no QA answer
    assert t0.metadata.get("source") == "Verified"
    # gold_context (a JSON STRING here) parses into 2 gold span sessions; all gold.
    assert len(t0.sessions) == 2
    assert t0.gold_memory_ids == [s.session_id for s in t0.sessions]
    assert "astropy/units/quantity.py:120-135" in t0.gold_memory_ids
    # second row's gold_context is a native list (one span).
    assert len(t1.sessions) == 1


def test_loader_registry_lists_all_five() -> None:
    benches = set(available())
    assert benches == {
        Benchmark.MEMORY_AGENT_BENCH,
        Benchmark.LONGMEMEVAL,
        Benchmark.SWE_CONTEXTBENCH,
        Benchmark.SWE_BENCH_CL,
        Benchmark.CONTEXTBENCH,
    }
    # Loose-string resolution goes through Benchmark.from_str.
    assert get_loader("LongMemEval").benchmark is Benchmark.LONGMEMEVAL
    assert get_loader("swe-bench-cl").benchmark is Benchmark.SWE_BENCH_CL
    assert get_loader("contextbench").benchmark is Benchmark.CONTEXTBENCH


def test_live_loaders_against_real_sources() -> None:
    """Opt-in: validate every loader against its real HF source (network).

    Skipped unless ``MEMEVAL_LIVE=1`` so the default suite stays offline + fast.
    Uses tiny limits (and LongMemEval's small ``oracle`` variant) so it is cheap.
    Run with: ``MEMEVAL_LIVE=1 python -m pytest tests/test_smoke.py -k live``.
    """
    import os
    if os.environ.get("MEMEVAL_LIVE") != "1":
        raise SkipTest("set MEMEVAL_LIVE=1 to run the live loader check")
    cases = [
        ("longmemeval", "longmemeval_oracle", TaskKind.QA),
        ("memoryagentbench", None, TaskKind.QA),
        ("swe_contextbench", None, TaskKind.CODE),
        ("swe_bench_cl", None, TaskKind.CODE),
        ("contextbench", None, TaskKind.CODE),
    ]
    for name, src, kind in cases:
        tasks = get_loader(name).load(src, limit=2)
        assert tasks, f"{name}: real source returned no tasks"
        t = tasks[0]
        assert t.kind is kind, f"{name}: expected {kind}, got {t.kind}"
        assert t.question, f"{name}: task has empty question"
        if kind is TaskKind.QA:
            assert t.answer, f"{name}: QA task missing gold answer"
        else:
            assert t.repo, f"{name}: CODE task missing repo"


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
        "contextbench",
    }
    assert cfg["longmemeval"]["captain"] == "Ken"
    assert cfg["contextbench"]["captain"] == "Brent"
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
# Schema -- MemoryItem.version (revision counter)
# --------------------------------------------------------------------------- #
def test_memory_item_version_default_and_round_trip() -> None:
    from memeval.trajectory import _memory_item_to_dict, _memory_item_from_dict
    # Default version is 1 for a fresh write.
    fresh = MemoryItem(item_id="m1", content="x")
    assert fresh.version == 1
    # An explicit (updated) version survives a serialize/deserialize round trip.
    updated = MemoryItem(item_id="m1", content="x v3", version=3)
    back = _memory_item_from_dict(_memory_item_to_dict(updated))
    assert back.version == 3
    # Legacy dicts without a version field default to 1 (back-compat).
    legacy = _memory_item_from_dict({"item_id": "m2", "content": "old"})
    assert legacy.version == 1


# --------------------------------------------------------------------------- #
# Grader -- CODE scoring (SWE-bench report parsing + offline heuristic)
# --------------------------------------------------------------------------- #
def _code_task(instance_id: str = "django__django-1", patch: str = "") -> Task:
    return Task(
        task_id=instance_id, benchmark=Benchmark.SWE_BENCH_CL, kind=TaskKind.CODE,
        question="fix the bug", patch=patch,
        metadata={"instance_id": instance_id},
    )


def test_grader_instance_id_and_prediction() -> None:
    from memeval import grader as G
    t = _code_task("astropy__astropy-42")
    assert G.instance_id_of(t) == "astropy__astropy-42"
    pred = G.build_prediction(t, "diff --git a b", model_name="memeval")
    assert pred == {
        "instance_id": "astropy__astropy-42",
        "model_name_or_path": "memeval",
        "model_patch": "diff --git a b",
    }


def test_grader_resolved_from_report_rule() -> None:
    from memeval import grader as G
    iid = "django__django-1"
    # Explicit resolved flag honored.
    assert G.resolved_from_report({iid: {"resolved": True}}, iid) is True
    assert G.resolved_from_report({iid: {"resolved": False}}, iid) is False
    # Derived from tests_status: all FAIL_TO_PASS + PASS_TO_PASS succeed -> True.
    ok = {iid: {"tests_status": {
        "FAIL_TO_PASS": {"success": ["t1", "t2"], "failure": []},
        "PASS_TO_PASS": {"success": ["t3"], "failure": []},
    }}}
    assert G.resolved_from_report(ok, iid) is True
    # Any PASS_TO_PASS regression -> not resolved.
    bad = {iid: {"tests_status": {
        "FAIL_TO_PASS": {"success": ["t1"], "failure": []},
        "PASS_TO_PASS": {"success": [], "failure": ["t3"]},
    }}}
    assert G.resolved_from_report(bad, iid) is False
    # Absent instance -> None (could not grade).
    assert G.resolved_from_report({}, iid) is None
    # Summary-report shape (swebench >=2.x make_run_report output).
    summ = {"resolved_ids": ["django__django-1"], "unresolved_ids": ["x__y-2"],
            "error_ids": [], "incomplete_ids": [], "empty_patch_ids": []}
    assert G.resolved_from_report(summ, "django__django-1") is True
    assert G.resolved_from_report(summ, "x__y-2") is False
    assert G.resolved_from_report(summ, "not__there-9") is None


def test_grader_overlap_offline() -> None:
    from memeval import grader as G
    gold = "diff --git a/f.py b/f.py\n+    return x + 1"
    t = _code_task(patch=gold)
    assert G.overlap_grader(t, gold) is True              # identical -> resolved
    assert G.overlap_grader(t, "totally unrelated text") is False
    # QA task (no patch) -> None (nothing to compare).
    qa = Task(task_id="q", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
              question="?", answer="a")
    assert G.overlap_grader(qa, "a") is None


def test_grader_registry_and_unavailable_skip() -> None:
    from memeval import grader as G
    assert G.get_grader("none")(_code_task(), "x") is None
    # swebench grader with skip policy returns None when Docker/swebench absent
    # (this environment has neither) instead of raising.
    g = G.get_grader("swebench", on_unavailable="skip")
    assert g(_code_task(patch="p"), "some patch") is None
    # Unknown grader name is a clear error.
    try:
        G.get_grader("bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# Aggregate -- the hypothesis scoreboard (Haiku+mem vs Opus no-mem)
# --------------------------------------------------------------------------- #
def _run_row(benchmark: str, model: str, memory: bool, *, accuracy: float,
             efficiency: float = 0.05, timestamp: str = "2026-06-17T00:00:00+00:00") -> dict:
    """Minimal ledger row for aggregate tests."""
    return {
        "benchmark": benchmark, "model": model, "memory": memory,
        "label": f"{model}{'+mem' if memory else ''}", "timestamp": timestamp,
        "metrics": {"accuracy": accuracy, "efficiency": efficiency,
                    "relevancy": 0.0, "recency": 0.0},
        "n_tasks": 2, "cost_usd": 0.001, "partial": False,
    }


def test_aggregate_win_when_haiku_mem_beats_opus_within_budget() -> None:
    data = {"runs": [
        _run_row("longmemeval", "claude-haiku-4-5", True, accuracy=0.8, efficiency=0.05),
        _run_row("longmemeval", "claude-opus-4-8", False, accuracy=0.5),
        _run_row("memoryagentbench", "claude-haiku-4-5", True, accuracy=0.9, efficiency=0.08),
        _run_row("memoryagentbench", "claude-opus-4-8", False, accuracy=0.6),
    ]}
    s = AGG.summarize(data)
    assert s["wins"] == 2
    assert s["criterion_met"] is True
    statuses = {b["benchmark"]: b["status"] for b in s["benchmarks"]}
    assert statuses["longmemeval"] == "win"
    assert statuses["memoryagentbench"] == "win"


def test_aggregate_over_budget_is_not_a_win() -> None:
    # Accuracy clears the bar, but memory overhead exceeds the 10% budget.
    data = {"runs": [
        _run_row("longmemeval", "claude-haiku-4-5", True, accuracy=1.0, efficiency=0.30),
        _run_row("longmemeval", "claude-opus-4-8", False, accuracy=0.0),
    ]}
    s = AGG.summarize(data)
    assert s["wins"] == 0
    assert s["criterion_met"] is False
    assert s["benchmarks"][0]["status"] == "over_budget"
    assert s["benchmarks"][0]["acc_win"] is True
    assert s["benchmarks"][0]["eff_ok"] is False


def test_aggregate_zero_vs_zero_is_not_a_win() -> None:
    data = {"runs": [
        _run_row("contextbench", "claude-haiku-4-5", True, accuracy=0.0, efficiency=0.04),
        _run_row("contextbench", "claude-opus-4-8", False, accuracy=0.0),
    ]}
    s = AGG.summarize(data)
    assert s["benchmarks"][0]["status"] == "loss"
    assert s["wins"] == 0


def test_aggregate_incomplete_when_baseline_missing() -> None:
    data = {"runs": [
        _run_row("longmemeval", "claude-haiku-4-5", True, accuracy=0.9, efficiency=0.05),
    ]}
    s = AGG.summarize(data)
    b = s["benchmarks"][0]
    assert b["comparable"] is False
    assert b["status"] == "incomplete"
    assert b["win"] is False


def test_aggregate_latest_row_wins_per_role() -> None:
    data = {"runs": [
        _run_row("longmemeval", "claude-haiku-4-5", True, accuracy=0.1,
                 efficiency=0.05, timestamp="2026-06-01T00:00:00+00:00"),
        _run_row("longmemeval", "claude-haiku-4-5", True, accuracy=0.9,
                 efficiency=0.05, timestamp="2026-06-17T00:00:00+00:00"),
        _run_row("longmemeval", "claude-opus-4-8", False, accuracy=0.5),
    ]}
    s = AGG.summarize(data)
    # The newer treatment row (0.9) is the one compared, not the stale 0.1.
    assert s["benchmarks"][0]["accuracy_treatment"] == 0.9
    assert s["benchmarks"][0]["status"] == "win"


def test_aggregate_format_summary_is_ascii() -> None:
    s = AGG.summarize({"runs": [
        _run_row("longmemeval", "claude-haiku-4-5", True, accuracy=0.8),
        _run_row("longmemeval", "claude-opus-4-8", False, accuracy=0.5),
    ]})
    text = AGG.format_summary(s)
    text.encode("ascii")  # must not raise — CLI output stays Windows-console safe
    assert "success criterion" in text


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
    Benchmark.CONTEXTBENCH: "contextbench.json",
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


def test_harness_run_contextbench_memory_off() -> None:
    _run_one(Benchmark.CONTEXTBENCH, False)


def test_harness_run_contextbench_memory_on() -> None:
    _run_one(Benchmark.CONTEXTBENCH, True)


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


def test_agent_adapter_protocol() -> None:
    from memeval.agent import AgentAdapter
    assert isinstance(EchoAgent(), AgentAdapter)


def test_run_agent_longmemeval_memory_on_off() -> None:
    fx = _fixture("longmemeval.json")
    for mem in (False, True):
        rr = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(), mem, path_or_id=fx)
        _assert_runresult(rr, Benchmark.LONGMEMEVAL, mem)
        assert rr.metadata.get("mode") == "agent"


def test_run_agent_is_multistep_and_writes_memory() -> None:
    fx = _fixture("longmemeval.json")
    rr = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(write_back=True), True, path_or_id=fx)
    # Memory-ON: the loop records retrieve + generate + write steps (multi-step).
    kinds = {s.kind for t in rr.trajectories for s in t.steps}
    assert {"retrieve", "generate", "write"} <= kinds
    # Memory-OFF: no retrieve / no write, but still generates.
    rr_off = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(), False, path_or_id=fx)
    kinds_off = {s.kind for t in rr_off.trajectories for s in t.steps}
    assert "generate" in kinds_off and "retrieve" not in kinds_off and "write" not in kinds_off


def test_run_agent_shared_store_accumulates() -> None:
    # A caller-supplied store is shared across tasks; agent write-backs land in it.
    fx = _fixture("longmemeval.json")
    store = InMemoryStore()
    rr = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(write_back=True), True,
                   path_or_id=fx, store=store)
    assert isinstance(rr, RunResult)
    assert any(getattr(m, "source", None) == "agent" for m in store.all())


def test_run_agent_result_forces_success_and_budget() -> None:
    fx = _fixture("longmemeval.json")
    # An AgentResult(success=True) overrides grading -> accuracy 1.0 for any text.
    agent = function_agent(lambda task, ctx: AgentResult(prediction="zzz", success=True))
    rr = run_agent(Benchmark.LONGMEMEVAL, agent, False, path_or_id=fx)
    assert _approx(rr.metrics.accuracy, 1.0)
    # Budget abort mid-loop -> partial RunResult (echo priced like opus).
    tracker = CostTracker(budget_usd=1e-9, pricing={"echo": {"in": 15.0, "out": 75.0}})
    rr2 = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(), True, path_or_id=fx, cost=tracker)
    assert rr2.budget_exceeded is True and rr2.partial is True


def test_results_ledger_round_trip() -> None:
    """A run appends a flat row to the ledger that the Results page can read."""
    from memeval.results import append_result, load_results
    from memeval.harness import run
    fx = _fixture("longmemeval.json")
    rr = run(Benchmark.LONGMEMEVAL, EchoModel(), True, path_or_id=fx)
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "results.json"
        rec = append_result(rr, ledger, run_id="t", notes="smoke")
        # Row shape the page renders.
        assert rec["benchmark"] == "longmemeval"
        assert rec["memory"] is True
        for kk in ("recency", "efficiency", "relevancy", "accuracy"):
            assert isinstance(rec["metrics"][kk], float)
        assert rec["n_tasks"] == rr.n_tasks and rec["run_id"] == "t"
        # Appends accumulate; file is valid JSON with a runs[] list.
        append_result(rr, ledger)
        data = load_results(ledger)
        assert len(data["runs"]) == 2 and data["schema"] >= 1
        json.dumps(data)  # serializable


def test_tracing_is_safe_noop() -> None:
    """The Langfuse shim never raises (and is a no-op when unconfigured)."""
    from memeval import tracing
    assert isinstance(tracing.enabled(), bool)
    with tracing.task_span("t", input="x") as span:
        span.step("generate", "g", output="y", tokens_in=1, tokens_out=1)
        span.score("accuracy", 1.0)
        span.update(output="z")
    tracing.flush()  # must not raise
    tracing.NOOP.step("k", "n"); tracing.NOOP.score("a", 1.0); tracing.NOOP.update()


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
