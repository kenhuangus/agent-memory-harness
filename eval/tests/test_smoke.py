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
import os
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
        relevancy       = (1.0+0.5/0.9)/2      (mean of per-step max-normed score)
        precision@k     = 1/2          = 0.5   (only the top hit (1.0) >= 0.7)
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
        # 6th benchmark (additive): VISTA Bench.
        Benchmark.VISTA,
    }
    # Loose-string resolution goes through Benchmark.from_str.
    assert get_loader("LongMemEval").benchmark is Benchmark.LONGMEMEVAL
    assert get_loader("swe-bench-cl").benchmark is Benchmark.SWE_BENCH_CL
    assert get_loader("contextbench").benchmark is Benchmark.CONTEXTBENCH
    assert get_loader("vista").benchmark is Benchmark.VISTA


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
    # precision@k is scorer-AGNOSTIC gold precision: of the 2 retrieved items,
    # exactly 1 (g1) is gold -> 1/2 = 0.5, independent of the BM25/Jaccard score.
    assert _approx(prec, 0.5), prec
    # No query embeddings supplied -> mean_similarity mirrors the gold precision
    # (no scorer-shape artifact).
    assert _approx(rel, 0.5), rel


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
    assert M.qa_match("The capital is Paris.", "Paris") is True  # whole-word containment
    assert M.qa_match("London", "Paris") is False


def test_qa_match_whole_word_not_digit_substring() -> None:
    """Regression: qa_match credits gold only on a WHOLE-word match, never a raw
    character substring. The classic failure was a numeric gold matching a digit
    embedded in a larger number.
    """
    # FALSE POSITIVE the old raw-substring grader produced: gold '7' inside '17'.
    assert M.qa_match("You packed 17 shirts for your trip.", "7") is False
    # The genuine whole-word match still passes.
    assert M.qa_match("You packed 7 shirts for your trip.", "7") is True
    # Multi-word gold must appear as a contiguous run of whole words, in order.
    assert M.qa_match("a one way ticket each way costs more", "each way") is True
    assert M.qa_match("the report covers each topic anyway", "each way") is False
    # Equality and the fuller-sentence containment from test_smoke still hold.
    assert M.qa_match("The capital is Paris.", "Paris") is True
    assert M.qa_match("Paris", "paris") is True
    assert M.qa_match("London", "Paris") is False
    # A numeric gold no longer matches a different number sharing a digit run.
    assert M.qa_match("the discount was 20% off", "10%") is False
    # Empty gold only matches empty prediction.
    assert M.qa_match("anything", "") is False
    assert M.qa_match("", "") is True


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
    # Scorer-agnostic gold precision: 1 of 2 retrieved items is gold -> 0.5
    # (see test_relevancy_metric). mean_similarity mirrors it (no query emb).
    assert _approx(m.relevancy, 0.5)
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


def test_run_agent_persists_is_gold_in_log() -> None:
    """run_agent annotates is_gold BEFORE logging, so the trajectory FILE carries
    gold flags. Regression: previously only the in-memory metrics pass set them,
    leaving every logged is_gold False — which silently breaks file-based recall
    analysis (a retrieved gold item reads as not-gold on disk)."""
    def _retrieving_agent(task, ctx):
        ctx.retrieve(task.question, k=100)  # surface the whole seeded store
        return AgentResult(prediction="x")
    agent = function_agent(_retrieving_agent)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.jsonl"
        with TrajectoryLogger(path, append=False) as log:
            run_agent(Benchmark.MEMORY_AGENT_BENCH, agent, memory=True,
                      path_or_id=_fixture("memoryagentbench.json"), limit=1,
                      logger=log)
        read = read_trajectory_list(path)
    flags = {ri.item_id: ri.is_gold
             for t in read for s in t.steps if s.kind == "retrieve"
             for ri in s.retrieved}
    assert flags.get("mab_s_move") is True    # the gold session, flagged IN THE FILE
    assert flags.get("mab_s_intro") is False  # a non-gold session stays unflagged


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
# OKF -- Open Knowledge Format interchange (POC)
# --------------------------------------------------------------------------- #
def test_okf_round_trip_lossless() -> None:
    from memeval import okf
    m = MemoryItem(
        item_id="orders", content="# Schema\nJoins [customers](/t/customers.md).",
        timestamp=1748443200.0, relevancy=0.8, session_id="s1", source="BigQuery Table",
        tags=["sales", "revenue"], tokens=42, version=3,
        metadata={"okf_title": "Orders", "okf_description": "One row per order.", "extra": 1},
    )
    back = okf.doc_to_memory_item(okf.memory_item_to_doc(m))
    for f in ("item_id", "content", "timestamp", "relevancy", "session_id", "tags",
              "tokens", "version"):
        assert getattr(back, f) == getattr(m, f), f
    assert back.metadata["extra"] == 1
    assert back.metadata["okf_links"] == [("customers", "/t/customers.md")]  # (anchor, target) link -> graph edge


def test_okf_bundle_export_import_and_conformance() -> None:
    from memeval import okf
    items = [
        MemoryItem(item_id="customers", content="Customer table.", source="BigQuery Table",
                   tags=["sales"], timestamp=1748443200.0),
        MemoryItem(item_id="wau", content="Weekly active users.", source="Metric",
                   timestamp=1748000000.0, version=2),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        manifest = okf.export_bundle(items, tmp)
        assert manifest["okf_version"] == "0.1" and manifest["n_concepts"] == 2
        assert (Path(tmp) / "index.md").exists() and (Path(tmp) / "log.md").exists()
        assert 'okf_version: "0.1"' in (Path(tmp) / "index.md").read_text(encoding="utf-8")
        assert okf.validate_bundle(tmp) == []  # conformant
        got = {i.item_id for i in okf.import_bundle(tmp)}
    assert got == {"customers", "wau"}


def test_okf_imports_foreign_google_sample_doc() -> None:
    # Verbatim shape of Google's okf/bundles/stackoverflow/tables/users.md:
    # multi-line description, block-list tags, quoted timestamp, no x_ keys.
    foreign = (
        "---\n"
        "type: BigQuery Table\n"
        "resource: https://bigquery.googleapis.com/v2/projects/x/datasets/so/tables/users\n"
        "title: Users\n"
        "description: This table contains information about users,\n"
        "  including profile information and activity metrics.\n"
        "tags:\n- Stack Overflow\n- users\n"
        "timestamp: '2026-05-28T23:32:24+00:00'\n"
        "---\n\n"
        "## Overview\nThe `users` table from [stackoverflow](../datasets/stackoverflow.md).\n"
    )
    from memeval import okf
    it = okf.doc_to_memory_item(foreign, fallback_id="users")
    assert it.source == "BigQuery Table"           # type -> source
    assert "Stack Overflow" in it.tags and "users" in it.tags
    assert it.timestamp > 0                          # ISO parsed to epoch
    assert it.metadata["okf_title"] == "Users"       # OKF semantics preserved
    assert it.metadata["okf_links"] == [("stackoverflow", "../datasets/stackoverflow.md")]
    assert "Overview" in it.content


def test_okf_store_is_a_memorystore() -> None:
    from memeval.protocols import MemoryStore
    from memeval import okf
    with tempfile.TemporaryDirectory() as tmp:
        store = okf.OKFStore(tmp)
        assert isinstance(store, MemoryStore)
        store.write(MemoryItem(item_id="m1", content="Paris is the capital of France",
                               tags=["geo"], timestamp=10.0))
        # persisted as an OKF doc on disk, and searchable through the store
        assert any(p.suffix == ".md" for p in Path(tmp).rglob("*.md"))
        hits = store.search("capital of France", k=3)
        assert hits and hits[0].item.item_id == "m1"
        # a fresh store autoloads the bundle from disk
        assert {i.item_id for i in okf.OKFStore(tmp).all()} == {"m1"}


# --------------------------------------------------------------------------- #
# Claude Code pipeline -- run benchmarks via the CLI with built-in / plugin memory
# --------------------------------------------------------------------------- #
def test_claudecode_run_bench_list_and_validate() -> None:
    """`memeval-bench --list-benchmarks` and bad-id validation work fully offline."""
    import contextlib
    import io
    from memeval.claudecode import run_bench

    # --list-benchmarks: exit 0, names every benchmark, no claude/dataset touched.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run_bench.main(["--list-benchmarks"])
    assert rc == 0
    out = buf.getvalue()
    for b in run_bench._ALL_BENCH:
        assert b in out

    # an unknown --benchmark fails fast (argparse error -> SystemExit 2).
    try:
        run_bench.main(["--benchmark", "nope", "--mode", "plugin"])
        raised = None
    except SystemExit as exc:
        raised = exc.code
    assert raised == 2


def test_claudecode_memory_service_recall_remember_and_log() -> None:
    from memeval.okf import OKFStore
    from memeval.claudecode.service import MemoryService
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "recall.jsonl"
        svc = MemoryService(OKFStore(Path(tmp) / "b"), log_path=log, default_k=3)
        svc.seed_items([MemoryItem(item_id="s1", content="Paris is the capital of France",
                                   timestamp=10.0)])
        hits = svc.recall("capital of France")
        assert hits and hits[0]["id"] == "s1"
        rid = svc.remember("a new fact", tags=["x"])
        recs = MemoryService.read_log(log)
        assert any(r["op"] == "recall" for r in recs)
        assert any(r["op"] == "remember" and r["id"] == rid for r in recs)


def test_claudecode_agent_builtin_writes_session_files() -> None:
    # builtin mode = Claude Code's OWN memory: the prior history is laid out as files
    # under sessions/ (Claude Code retrieves over them with its native Grep/Read
    # tools), with a small CLAUDE.md pointer and no MCP — no whole-history dump.
    from memeval.claudecode.agent import ClaudeCodeAgent
    from memeval.claudecode.cli import ClaudeResult
    seen: dict = {}

    def fake(prompt, *, cwd, mcp_config=None, **kw):
        cm = Path(cwd) / "CLAUDE.md"
        seen["claude_md"] = cm.read_text(encoding="utf-8") if cm.exists() else None
        sess = Path(cwd) / "sessions"
        files = sorted(sess.glob("*.md")) if sess.exists() else []
        seen["n_session_files"] = len(files)
        seen["session_text"] = "\n".join(f.read_text(encoding="utf-8") for f in files)
        seen["mcp"] = mcp_config
        return ClaudeResult(text="Berlin", tokens_in=12, tokens_out=2)

    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="builtin", runner=fake, workdir=tmp)
        rr = run_agent(Benchmark.MEMORY_AGENT_BENCH, agent, memory=True,
                       path_or_id=_fixture("memoryagentbench.json"), limit=1,
                       seed_sessions=False)
    assert rr.n_tasks == 1
    assert seen["claude_md"] and "sessions/" in seen["claude_md"]  # small pointer, not a dump
    assert seen["n_session_files"] == 2                            # history laid out as files
    assert seen["session_text"].strip()                            # files carry the content
    assert seen["mcp"] is None                                     # builtin uses no MCP
    assert rr.metrics.accuracy == 1.0                             # "Berlin" matches the gold


def test_claudecode_agent_plugin_records_retrieval() -> None:
    # plugin mode = our memory: the fake CLI calls the configured server, whose
    # recall log the agent reads back into the trajectory.
    import json as _json
    from memeval.okf import OKFStore
    from memeval.claudecode.agent import ClaudeCodeAgent
    from memeval.claudecode.cli import ClaudeResult
    from memeval.claudecode.platform import ClaudeRuntime
    from memeval.claudecode.service import MemoryService
    seen: dict = {}

    def fake(prompt, *, cwd, mcp_config=None, allowed_tools=None, strict_mcp=False, **kw):
        seen["tools"] = allowed_tools
        seen["strict_mcp"] = strict_mcp
        cfg = _json.loads(Path(mcp_config).read_text(encoding="utf-8"))
        a = cfg["mcpServers"]["memeval-memory"]["args"]
        bundle = a[a.index("--bundle") + 1]
        log = a[a.index("--log") + 1]
        svc = MemoryService(OKFStore(bundle), log_path=log)
        hits = svc.recall(prompt, k=5)          # simulate the agent retrieving
        return ClaudeResult(text=(hits[0]["content"] if hits else "?"), tokens_in=20, tokens_out=4)

    # pin a native runtime so the bundle path stays local (the fake runner opens it here)
    native = ClaudeRuntime(kind="native", exe="claude", python="python")
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin", runner=fake, runtime=native,
                                workdir=tmp, transport="stdio")
        rr = run_agent(Benchmark.MEMORY_AGENT_BENCH, agent, memory=True,
                       path_or_id=_fixture("memoryagentbench.json"), limit=1,
                       seed_sessions=False)
    kinds = [s.kind for t in rr.trajectories for s in t.steps]
    assert "retrieve" in kinds and "generate" in kinds          # retrieval attributed
    assert seen["tools"] == ["mcp__memeval-memory__memory_recall",
                             "mcp__memeval-memory__memory_remember"]
    assert seen["strict_mcp"] is True                            # plugin isolates MCP


def test_claudecode_agent_plugin_real_records_retrieval_from_events() -> None:
    # plugin-real mode = the SHIPPING plugin as a black box. The fake CLI stands in
    # for the installed plugin: it writes a recall event (with meta.hits) to the
    # plugin's own events stream, exactly as the cookbook-memory MCP server would,
    # and the agent attributes that retrieval to the trajectory from meta.hits.
    #
    # Stdlib-only: plugin-real uses a fake runner (no real `claude`, no `cookbook_memory`
    # import) and the harness never touches the store — the plugin owns it — so this runs
    # in CI as written. With no shared substrate configured, the store is the per-task dir.
    import json as _json
    from memeval.claudecode.agent import ClaudeCodeAgent
    from memeval.claudecode.cli import ClaudeResult
    from memeval.claudecode.platform import ClaudeRuntime
    seen: dict = {}

    def fake(prompt, *, cwd, **kw):
        seen["cwd"] = str(cwd)
        # Simulate the installed plugin's MCP server logging a recall to its events
        # stream under ${CLAUDE_PROJECT_DIR}/.cookbook-memory (here: the run dir).
        store = Path(cwd) / ".cookbook-memory"
        store.mkdir(parents=True, exist_ok=True)
        ev = {
            "ts": 1.0, "op": "recall", "ids": ["m1"], "query": prompt,
            "meta": {"hits": [{"id": "m1", "content": "sqlite was chosen",
                               "score": 0.9, "rank": 0, "tokens": 3, "timestamp": 1.0}]},
        }
        (store / "events.jsonl").write_text(_json.dumps(ev) + "\n")
        return ClaudeResult(text="sqlite", tokens_in=20, tokens_out=4)

    native = ClaudeRuntime(kind="native", exe="claude", python="python")
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin-real", runner=fake, runtime=native,
                                workdir=tmp)
        rr = run_agent(Benchmark.MEMORY_AGENT_BENCH, agent, memory=True,
                       path_or_id=_fixture("memoryagentbench.json"), limit=1,
                       seed_sessions=False)
    kinds = [s.kind for t in rr.trajectories for s in t.steps]
    assert "retrieve" in kinds and "generate" in kinds      # attributed from meta.hits
    # the run executed in the per-task dir, where the plugin's store/events live
    assert seen["cwd"]


def test_claudecode_platform_path_translation_and_argv() -> None:
    import os
    from memeval.claudecode.platform import to_wsl_path, ClaudeRuntime
    from memeval.claudecode.cli import build_argv
    # Windows -> WSL path translation (drive-letter aware; POSIX passes through).
    assert to_wsl_path(r"C:\Users\x\t") == "/mnt/c/Users/x/t"
    assert to_wsl_path("/home/u/x") == "/home/u/x"
    # A relative path must resolve to an ABSOLUTE posix path, never a bare
    # relative string (which `wsl --cd` rejects with E_INVALIDARG). On Windows
    # this is /mnt/<drive>/...; on a POSIX CI host it's /<abs>/... — both start
    # with "/" and contain no "..".
    rel = to_wsl_path(os.path.join("..", "runs", "out"))
    assert rel.startswith("/") and ".." not in rel and "\\" not in rel
    # native argv runs claude directly in cwd
    nat = ClaudeRuntime(kind="native", exe="claude")
    argv, cwd = build_argv(nat, "hi", cwd="/work", model="claude-haiku-4-5")
    assert argv[:3] == ["claude", "-p", "hi"] and cwd == "/work"
    # WSL argv wraps with `wsl -d <distro> --cd <wslpath> -- <exe>`, translates mcp path
    wsl = ClaudeRuntime(kind="wsl", exe="/home/k/.local/bin/claude", distro="Ubuntu")
    argv, cwd = build_argv(wsl, "hi", cwd=r"C:\w", mcp_config=r"C:\w\.mcp.json", strict_mcp=True)
    assert argv[0] == "wsl" and "--cd" in argv and "/mnt/c/w" in argv
    assert "/home/k/.local/bin/claude" in argv and cwd is None
    assert "/mnt/c/w/.mcp.json" in argv and "--strict-mcp-config" in argv


def test_claudecode_primed_stream_json_argv_and_parse() -> None:
    # The plugin (MCP) path drives a *priming turn* over stream-json I/O to close
    # the startup race where `claude -p` generates before the MCP tools register.
    from memeval.claudecode.platform import ClaudeRuntime
    from memeval.claudecode import cli
    # native primed argv: stream-json I/O, NO `-p <prompt>` positional (prompt is
    # fed via stdin), MCP config + strict flag carried through.
    nat = ClaudeRuntime(kind="native", exe="claude")
    argv, cwd = cli.build_argv_primed(nat, cwd="/work", model="claude-haiku-4-5",
                                      mcp_config="/work/.mcp.json", strict_mcp=True)
    assert argv[:2] == ["claude", "-p"] and cwd == "/work"
    assert "--input-format" in argv and "stream-json" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "/work/.mcp.json" in argv and "--strict-mcp-config" in argv
    # the prompt must NOT appear as a positional (it goes to stdin)
    assert "hi" not in argv
    # WSL primed argv wraps with `wsl -d <distro> --cd ... -- env -u ... claude -p ...`
    wsl = ClaudeRuntime(kind="wsl", exe="/home/k/.local/bin/claude", distro="Ubuntu")
    wargv, wcwd = cli.build_argv_primed(wsl, cwd=r"C:\w", mcp_config=r"C:\w\.mcp.json",
                                        strict_mcp=True)
    assert wargv[0] == "wsl" and wcwd is None and "/mnt/c/w/.mcp.json" in wargv
    assert "--input-format" in wargv and "stream-json" in wargv

    # stdin serialization: priming turn first, then the real prompt, each a JSON
    # `user` event on its own line.
    import json as _json
    stream = cli._stream_json_input([cli._PRIME_MESSAGE, "what is the secret?"])
    lines = [l for l in stream.splitlines() if l.strip()]
    assert len(lines) == 2
    first = _json.loads(lines[0]); second = _json.loads(lines[1])
    assert first["type"] == "user" and first["message"]["content"] == cli._PRIME_MESSAGE
    assert second["message"]["content"] == "what is the secret?"

    # parser returns the LAST result event (the real answer, not the priming reply)
    # and sums usage across turns.
    out = "\n".join([
        _json.dumps({"type": "system", "subtype": "init"}),
        _json.dumps({"type": "result", "result": "READY",
                     "usage": {"input_tokens": 5, "output_tokens": 1}}),
        _json.dumps({"type": "assistant", "message": {"content": "x"}}),
        _json.dumps({"type": "result", "result": "the secret is MAUVE-42",
                     "usage": {"input_tokens": 30, "output_tokens": 7},
                     "total_cost_usd": 0.01, "num_turns": 1}),
    ])
    res = cli._parse_stream_json(out)
    assert res.text == "the secret is MAUVE-42"      # last result, not "READY"
    assert res.tokens_in == 35 and res.tokens_out == 8   # summed across turns


def test_claudecode_plugin_mcp_json_uses_wsl_python_and_paths() -> None:
    # Under a WSL runtime the .mcp.json must use the WSL python + /mnt paths so the
    # server claude spawns inside WSL can find memeval and the bundle.
    import json as _json
    from memeval.claudecode.agent import ClaudeCodeAgent
    from memeval.claudecode.cli import ClaudeResult
    from memeval.claudecode.platform import ClaudeRuntime
    seen: dict = {}

    def fake(prompt, *, cwd, mcp_config=None, **kw):
        seen["cfg"] = _json.loads(Path(mcp_config).read_text(encoding="utf-8"))
        return ClaudeResult(text="ok", tokens_in=1, tokens_out=1)

    rt = ClaudeRuntime(kind="wsl", exe="/c/claude", distro="Ubuntu", python="/v/bin/python")
    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="plugin", runner=fake, runtime=rt, workdir=tmp)
        run_agent(Benchmark.MEMORY_AGENT_BENCH, agent, memory=True,
                  path_or_id=_fixture("memoryagentbench.json"), limit=1, seed_sessions=False)
    srv = seen["cfg"]["mcpServers"]["memeval-memory"]
    assert srv["command"] == "/v/bin/python"                       # WSL python
    args = srv["args"]
    bundle = args[args.index("--bundle") + 1]
    assert bundle.startswith("/mnt/") or bundle.startswith("/")     # translated to POSIX


def test_claudecode_strips_api_key_subscription_only() -> None:
    # No LLM API key may reach the claude invocation — benchmark runs use the
    # Claude Code subscription only.
    import os
    from memeval.claudecode import cli
    from memeval.claudecode.platform import ClaudeRuntime
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "tok-test"
    # Force the config sandbox off for the whole test so it asserts only the API-key
    # behavior, regardless of whether a local eval/.claude-sandbox/ has been built
    # (which would otherwise make _clean_env / the WSL prefix inject CLAUDE_CONFIG_DIR).
    prev_sbx = os.environ.get("MEMEVAL_SANDBOX")
    os.environ["MEMEVAL_SANDBOX"] = "0"
    try:
        env = cli._clean_env(True)
        assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
        assert cli._clean_env(False) is None     # opt-out keeps the inherited env
        # WSL path strips via `env -u` by default
        wsl = ClaudeRuntime(kind="wsl", exe="/c/claude", distro="Ubuntu")
        argv, _ = cli.build_argv(wsl, "hi", cwd=r"C:\w")
        assert "env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN" in " ".join(argv)
        # explicit opt-out drops the env-strip prefix (the claude exe follows `--`)
        argv2, _ = cli.build_argv(wsl, "hi", cwd=r"C:\w", strip_api_key=False)
        assert argv2[argv2.index("--") + 1] == "/c/claude"
        assert "ANTHROPIC_API_KEY" not in " ".join(argv2)
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        if prev_sbx is None:
            os.environ.pop("MEMEVAL_SANDBOX", None)
        else:
            os.environ["MEMEVAL_SANDBOX"] = prev_sbx


def test_group_aware_draw_prefers_whole_largest_groups() -> None:
    """Group-aware selection fills the limit with whole groups, largest first,
    skipping singletons and preserving within-group order (priors first)."""
    from memeval.agent import _select_group_aware

    def mk(gid, i):
        return Task(task_id=f"{gid}-{i}", benchmark=Benchmark.SWE_CONTEXTBENCH,
                    kind=TaskKind.CODE, question="q", group_id=gid)

    tasks = (
        [mk("singleA", 0), mk("singleB", 0)]          # two singletons (no priors)
        + [mk("big", i) for i in range(5)]            # one 5-task group
        + [mk("mid", i) for i in range(3)]            # one 3-task group
    )
    sel = _select_group_aware(tasks, limit=6)
    assert len(sel) == 6
    gids = [t.group_id for t in sel]
    # Largest group ('big', 5) fully included, then a 1-task prefix of 'mid'.
    assert gids.count("big") == 5 and gids.count("mid") == 1
    assert "singleA" not in gids and "singleB" not in gids  # singletons skipped
    # Within-group order preserved (priors precede dependents).
    assert [t.task_id for t in sel if t.group_id == "big"] == [f"big-{i}" for i in range(5)]


def test_claudecode_per_benchmark_limit_floors() -> None:
    """Bare runs use each benchmark's long-memory floor; --limit overrides; 0 = all."""
    from memeval.claudecode import run_bench as rb
    # Every benchmark the runner sweeps has an explicit floor of >=1 entry.
    for b in rb._ALL_BENCH:
        assert rb.DEFAULT_FLOORS.get(b, rb.DEFAULT_MIN_ENTRIES) >= 1
    # CL code bench draws wider than a single QA question (memory is cross-entry).
    assert rb.DEFAULT_FLOORS["swe_bench_cl"] >= rb.DEFAULT_FLOORS["longmemeval"]
    # Resolution: None -> floor; positive -> itself; 0/negative -> None (whole set).
    assert rb._resolve_limit("swe_bench_cl", None) == rb.DEFAULT_FLOORS["swe_bench_cl"]
    assert rb._resolve_limit("longmemeval", 7) == 7
    assert rb._resolve_limit("longmemeval", 0) is None
    assert rb._resolve_limit("unknown_bench", None) == rb.DEFAULT_MIN_ENTRIES
    # Group-aware draw: auto -> on for cross-entry-memory benches, off for QA;
    # explicit flat/group force it either way.
    assert rb._resolve_group_aware("swe_contextbench", "auto") is True
    assert rb._resolve_group_aware("swe_bench_cl", "auto") is True
    assert rb._resolve_group_aware("longmemeval", "auto") is False
    assert rb._resolve_group_aware("longmemeval", "group") is True
    assert rb._resolve_group_aware("swe_contextbench", "flat") is False


def test_claudecode_agent_naming_and_validation() -> None:
    from memeval.claudecode.agent import ClaudeCodeAgent
    a = ClaudeCodeAgent(model="claude-haiku-4-5", memory_mode="plugin")
    assert a.name == "claude-code:claude-haiku-4-5:plugin"
    assert a.price_in > 0 and a.price_out > 0     # priced from cost.PRICING
    try:
        ClaudeCodeAgent(memory_mode="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


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
    # Summary-report shape (a make_run_report-style summary).
    summ = {"resolved_ids": ["django__django-1"], "unresolved_ids": ["x__y-2"],
            "error_ids": [], "incomplete_ids": [], "empty_patch_ids": []}
    assert G.resolved_from_report(summ, "django__django-1") is True
    assert G.resolved_from_report(summ, "x__y-2") is False
    assert G.resolved_from_report(summ, "not__there-9") is None


def test_grader_django_parse_docstring_and_selector_filter() -> None:
    """The django runtests parser must survive verbosity=2 docstring formatting
    (the ``... ok`` lands on the docstring line, not the method line) and the
    selector filter must drop leaked docstrings from PASS_TO_PASS.

    Regression for django__django-9296: a test WITH a docstring printed as two
    lines was misclassified as a failure, and the leaked docstring selector
    ``"Paginator.get_page() with an empty object_list."`` triggered a phantom
    ``ERROR: ... _FailedTest`` that flipped the whole run to FAILED.
    """
    from memeval import grader as G

    # --- is_django_selector: accept real labels, reject prose/docstrings. ---
    assert G.is_django_selector("test_get_page (pagination.tests.PaginationTests)")
    assert G.is_django_selector("pagination.tests.PaginationTests.test_get_page")
    assert G.is_django_selector("pagination.tests")
    # Leaked docstring (the exact 9296 contaminant) and other prose -> rejected.
    assert not G.is_django_selector("Paginator.get_page() with an empty object_list.")
    assert not G.is_django_selector("A paginator page acts like a standard sequence.")
    assert not G.is_django_selector("")

    # --- _parse_django on canned verbosity=2 output (docstring two-line form). ---
    # test_no_doc has no docstring (one line); test_get_page has a docstring whose
    # "... ok" is on the SECOND line and never repeats the method name. A pre-fix
    # parser marked test_get_page as a failure.
    stderr = (
        "test_no_doc (pagination.tests.PaginationTests.test_no_doc) ... ok\n"
        "test_get_page (pagination.tests.PaginationTests.test_get_page)\n"
        "Paginator.get_page() returns a valid page even with invalid page ... ok\n"
        "----------------------------------------------------------------------\n"
        "Ran 2 tests in 0.001s\n"
        "\n"
        "OK\n"
    )
    f2p = ["test_no_doc (pagination.tests.PaginationTests)"]
    p2p = ["test_get_page (pagination.tests.PaginationTests)"]
    status = G._parse_django("", stderr, f2p, p2p)
    assert status["FAIL_TO_PASS"]["success"] == f2p
    assert status["FAIL_TO_PASS"]["failure"] == []
    assert status["PASS_TO_PASS"]["success"] == p2p
    assert status["PASS_TO_PASS"]["failure"] == []
    iid = "django__django-9296"
    assert G.resolved_from_report({iid: {"tests_status": status}}, iid) is True

    # --- A genuine FAIL: banner must still classify that test as a failure. ---
    fail_err = (
        "test_get_page (pagination.tests.PaginationTests.test_get_page)\n"
        "Paginator.get_page() returns a valid page ... FAIL\n"
        "======================================================================\n"
        "FAIL: test_get_page (pagination.tests.PaginationTests.test_get_page)\n"
        "----------------------------------------------------------------------\n"
        "AssertionError: ...\n"
        "Ran 1 test in 0.001s\n"
        "FAILED (failures=1)\n"
    )
    bad = G._parse_django("", fail_err, [], p2p)
    assert bad["PASS_TO_PASS"]["failure"] == p2p
    assert bad["PASS_TO_PASS"]["success"] == []

    # --- A selector that never ran (not in output) -> failure (honest default). ---
    never = G._parse_django("", "OK\n",
                            ["test_absent (pagination.tests.PaginationTests)"], [])
    assert never["FAIL_TO_PASS"]["failure"] == \
        ["test_absent (pagination.tests.PaginationTests)"]


def test_pytest_selector_filter_and_parse() -> None:
    """The pytest path's two data/parse hazards: a leaked ``[100%]`` progress token
    that aborts the whole run, and a node id literally named ``test_failed`` that a
    substring parser misreads as a failure."""
    from memeval import grader as G

    # --- is_pytest_selector: accept node ids, reject captured progress junk. ---
    assert G.is_pytest_selector("testing/test_x.py::TestC::test_y")
    assert G.is_pytest_selector("testing/test_x.py::test_y[param]")
    assert G.is_pytest_selector("testing/test_x.py::test_y[test_input1-expected1]")
    assert not G.is_pytest_selector("[100%]")     # leaked progress bar (the contaminant)
    assert not G.is_pytest_selector("")
    assert not G.is_pytest_selector("4 passed in 1.5s")
    # Truncated parametrized ids (param contained ", " -> capture split off the rest,
    # leaving an unbalanced bracket). Unrecoverable; pytest reports "not found" and
    # aborts the whole run, so these must be dropped.
    assert not G.is_pytest_selector("testing/test_skipping.py::TestXFail::test_xfail_raises[(AttributeError,")
    assert not G.is_pytest_selector('testing/test_skipping.py::TestSkipif::test_skipif_reporting["hasattr(sys,')
    assert not G.is_pytest_selector("testing/test_x.py::test_xfail_raises[TypeError-IndexError-*1")

    # --- _parse_pytest reads pytest -rA "<STATUS> <nodeid>" summary lines. ---
    # Crucially, a node id NAMED test_failed must be read by the leading STATUS word,
    # not a substring scan (the latter sees "FAILED" inside "test_failed").
    f2p = ["testing/test_pastebin.py::TestPaste::test_create_new_paste"]
    p2p = [
        "testing/test_pastebin.py::TestPasteCapture::test_failed",   # PASSED despite the name
        "testing/test_pastebin.py::TestPasteCapture::test_all",
    ]
    out = (
        "=========================== short test summary info ===========================\n"
        "PASSED testing/test_pastebin.py::TestPaste::test_create_new_paste\n"
        "PASSED testing/test_pastebin.py::TestPasteCapture::test_failed\n"
        "PASSED testing/test_pastebin.py::TestPasteCapture::test_all\n"
        "=========================== 3 passed in 1.43 seconds ===========================\n"
    )
    status = G._parse_pytest(out, f2p, p2p)
    assert status["FAIL_TO_PASS"]["success"] == f2p
    assert status["PASS_TO_PASS"]["failure"] == [], \
        "test_failed must be read as PASSED, not misparsed via substring"
    assert set(status["PASS_TO_PASS"]["success"]) == set(p2p)

    # A genuinely FAILED selector is classified as failure; a parametrized id matches.
    out2 = (
        "FAILED testing/test_x.py::test_a - AssertionError: boom\n"
        "PASSED testing/test_x.py::test_b[case1]\n"
    )
    st2 = G._parse_pytest(out2, ["testing/test_x.py::test_a"],
                          ["testing/test_x.py::test_b"])
    assert st2["FAIL_TO_PASS"]["failure"] == ["testing/test_x.py::test_a"]
    assert st2["PASS_TO_PASS"]["success"] == ["testing/test_x.py::test_b"]


def test_patch_target_files() -> None:
    """`patch_target_files` extracts the +++ b/ post-image paths a diff modifies —
    used to revert gold-test files to base before applying the gold test_patch."""
    from memeval import grader as G

    patch = (
        "diff --git a/testing/test_a.py b/testing/test_a.py\n"
        "--- a/testing/test_a.py\n+++ b/testing/test_a.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/src/pkg/mod.py b/src/pkg/mod.py\n"
        "--- a/src/pkg/mod.py\n+++ b/src/pkg/mod.py\n"
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    assert G.patch_target_files(patch) == ["testing/test_a.py", "src/pkg/mod.py"]
    # /dev/null post-image (a deletion) is skipped; empty patch -> [].
    assert G.patch_target_files("--- a/f\n+++ /dev/null\n") == []
    assert G.patch_target_files("") == []


def test_auto_grader_prefers_swebench_when_available() -> None:
    """`auto` picks the swebench grader iff the swebench extra imports, else
    LocalExecGrader — never breaking grading on a host without the extra.

    Uses manual attribute save/restore (no pytest `monkeypatch`) so this file still
    runs under the stdlib-only smoke harness (`python tests/test_smoke.py`)."""
    import argparse

    from memeval import grader as Gmod
    from memeval.claudecode import run_bench as RB
    from memeval.grader import LocalExecGrader

    args = argparse.Namespace(grader="auto", grader_timeout=60)
    bench = next(iter(RB._LOCAL_EXEC_BENCH))

    _orig_avail = RB._swebench_available
    _orig_get = Gmod.get_grader
    try:
        # Extra absent -> fall back to LocalExecGrader (real instance, no swebench).
        RB._swebench_available = lambda: False
        assert isinstance(RB._make_grader(bench, args), LocalExecGrader)

        # Extra present -> route to the "swebench" grader name (capture without
        # importing the real package).
        chosen: dict = {}
        RB._swebench_available = lambda: True
        Gmod.get_grader = lambda name, **kw: chosen.setdefault("name", name)
        RB._make_grader(bench, args)
        assert chosen["name"] == "swebench"
    finally:
        RB._swebench_available = _orig_avail
        Gmod.get_grader = _orig_get


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


def test_grader_registry_and_local() -> None:
    from memeval import grader as G
    # 'none' -> always-None grader (leave CODE ungraded).
    assert G.get_grader("none")(_code_task(), "x") is None
    # 'local' -> a LocalExecGrader instance.
    g = G.get_grader("local")
    assert isinstance(g, G.LocalExecGrader)
    # LocalExecGrader returns None for QA tasks (not its concern).
    qa = Task(task_id="q", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
              question="?", answer="a")
    assert g(qa, "a") is None
    # Empty prediction on a CODE task -> False (a real miss, no patch produced).
    assert g(_code_task(patch="p"), "") is False
    # Unknown grader name is a clear error.
    try:
        G.get_grader("bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_run_agent_grading_exception_is_ungraded_not_a_miss() -> None:
    """When the GRADER itself raises (e.g. the local test environment could not be
    built), the task is recorded UNGRADED (success=None), not a counted CODE
    failure. metrics.accuracy then excludes it from the denominator rather than
    dishonestly depressing the resolved rate.
    """
    def _raising_grader(task, prediction):
        raise RuntimeError("local test environment could not be built")

    fx = _fixture("swe_contextbench.json")
    rr = run_agent(Benchmark.SWE_CONTEXTBENCH, EchoAgent(), memory=False,
                   path_or_id=fx, limit=1, grader=_raising_grader)
    # The grading exception leaves success unset (None == ungraded), not False.
    assert all(t.success is None for t in rr.trajectories)
    # Ungraded tasks are excluded from accuracy's denominator -> 0.0 here (no
    # graded trajectory), never a fake miss.
    assert _approx(rr.metrics.accuracy, 0.0)
    # A clean comparison: a grader that returns False IS a counted miss.
    rr_false = run_agent(Benchmark.SWE_CONTEXTBENCH, EchoAgent(), memory=False,
                         path_or_id=fx, limit=1,
                         grader=lambda task, pred: False)
    assert all(t.success is False for t in rr_false.trajectories)
    assert _approx(rr_false.metrics.accuracy, 0.0)
    # accuracy() sees the False run as graded (denominator 1) but the raising run
    # as ungraded (denominator 0) -- the honest distinction this fix restores.
    assert M.accuracy(rr.trajectories) == 0.0 and rr.trajectories[0].success is None


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


# --------------------------------------------------------------------------- #
# BM25 scorer -- the gold-recall fix (replaces length-coupled Jaccard)
# --------------------------------------------------------------------------- #
def test_bm25_long_gold_outranks_short_noise() -> None:
    """The exact Jaccard failure mode: a LONG gold turn containing the rare query
    term must outrank a SHORT generic doc that shares only a common/high-df token.

    Under Jaccard the long gold's huge ``|q ∪ d|`` denominator crushed it below
    the short doc; under BM25 (IDF up-weights the rare term, length saturates) it
    wins. This is the load-bearing gold-recall regression guard.
    """
    store = InMemoryStore()
    # "report" is the common high-df token (appears in both). "wittgenstein" is the
    # rare discriminative term that uniquely identifies the gold turn.
    long_gold = (
        "user said in the meeting the quarterly report covers many topics "
        "including budget timeline staffing and the philosopher wittgenstein "
        "was mentioned as the favorite author with lots of additional padding "
        "context filler words here to make this turn genuinely long indeed"
    )
    short_noise = "the report is short"
    store.write(MemoryItem(item_id="noise", content=short_noise, timestamp=1.0))
    store.write(MemoryItem(item_id="gold", content=long_gold, timestamp=1.0))

    hits = store.search("wittgenstein report", k=5)
    assert hits[0].item_id == "gold", [h.item_id for h in hits]
    # And the gold's BM25 score is strictly positive (it matched the rare term).
    assert hits[0].score > 0.0


def test_bm25_score_independent_of_padding() -> None:
    """Length-normalization guard: padding the gold doc with N extra non-query
    tokens keeps it ranked #1 over a short doc sharing only a common token.

    (Jaccard's |q ∪ d| made score collapse as padding grew -- BM25 does not.)
    """
    base_gold = "the user favorite author is wittgenstein"
    pad = " ".join(["filler"] * 200)  # 200 extra non-query tokens
    short_other = "the user wrote a report"  # shares only the common 'user'/'the'

    store = InMemoryStore()
    store.write(MemoryItem(item_id="gold", content=base_gold + " " + pad, timestamp=1.0))
    store.write(MemoryItem(item_id="other", content=short_other, timestamp=1.0))

    hits = store.search("wittgenstein author", k=5)
    assert hits[0].item_id == "gold", [h.item_id for h in hits]


def test_bm25_deterministic_across_write_order() -> None:
    """Two stores ingesting the SAME items in DIFFERENT write order return
    identical ranking AND identical scores for a multi-term query.

    Guards the sorted-query-term accumulation + df-stability determinism.
    """
    rows = [
        ("a", "alpha beta gamma shared token here", 1.0, 5.0),
        ("b", "beta shared token plus more shared content", 1.0, 6.0),
        ("c", "token alone shared", 1.0, 7.0),
        ("d", "gamma delta epsilon zeta", 1.0, 8.0),
    ]
    s1 = InMemoryStore()
    for item_id, content, rel, ts in rows:
        s1.write(MemoryItem(item_id=item_id, content=content, relevancy=rel, timestamp=ts))
    s2 = InMemoryStore()
    for item_id, content, rel, ts in reversed(rows):  # different write order
        s2.write(MemoryItem(item_id=item_id, content=content, relevancy=rel, timestamp=ts))

    q = "shared token gamma"
    h1 = s1.search(q, k=10)
    h2 = s2.search(q, k=10)
    assert [h.item_id for h in h1] == [h.item_id for h in h2]
    assert [h.score for h in h1] == [h.score for h in h2]  # exact float equality


def test_bm25_idf_coverage_breaks_ties_toward_rarer_term() -> None:
    """When two docs tie on BM25 primary score, the one matching the rarer
    (higher-IDF) query term ranks first via the IDF-coverage secondary key.

    Setup: query has two terms -- ``common`` (in many docs => low IDF) and
    ``rare`` (in one doc => high IDF). Two candidate docs each match exactly one
    query term once, with identical length, so their BM25 *per-matched-term*
    structure is symmetric except for that term's IDF. The rare-term doc wins.
    """
    store = InMemoryStore()
    # Build up df so 'common' is high-df (low IDF) and 'rare' is low-df (high IDF).
    for i in range(5):
        store.write(MemoryItem(item_id=f"bg{i}", content=f"common filler{i} extra{i}",
                               timestamp=1.0, relevancy=0.5))
    # Two equal-length candidates: one matches 'common', one matches 'rare'.
    store.write(MemoryItem(item_id="hits_common", content="common alpha beta",
                           timestamp=1.0, relevancy=0.5))
    store.write(MemoryItem(item_id="hits_rare", content="rare alpha beta",
                           timestamp=1.0, relevancy=0.5))

    hits = store.search("common rare", k=20)
    ids = [h.item_id for h in hits]
    assert ids.index("hits_rare") < ids.index("hits_common"), ids


def test_bm25_empty_query_inmemory_zero_padded() -> None:
    """InMemoryStore's empty-query contract: returns zero-padded results (NOT [])
    with every score 0.0 -- distinct from MarkdownStore which returns []."""
    store = InMemoryStore()
    store.write(MemoryItem(item_id="a", content="anything at all", timestamp=1.0))
    store.write(MemoryItem(item_id="b", content="something else", timestamp=2.0))
    hits = store.search("   ", k=5)  # whitespace-only -> no tokens
    assert len(hits) == 2  # zero-padded, not empty
    assert all(h.score == 0.0 for h in hits)


def test_relevancy_is_scorer_agnostic_gold_precision() -> None:
    """metrics.relevancy.precision@k is GOLD precision, independent of the raw
    retriever-score magnitude. The same retrieval (one gold, one non-gold)
    yields precision 0.5 whether the scorer emits tiny Jaccard-era scores or
    large BM25-magnitude scores -- the metric reflects WHAT was retrieved, not
    the scorer's distribution shape.
    """
    task = Task(task_id="t", benchmark=Benchmark.LONGMEMEVAL, kind=TaskKind.QA,
                question="q", gold_memory_ids=["g"])

    def _two_item_traj(s_gold: float, s_noise: float) -> Trajectory:
        tr = Trajectory(task_id="t", benchmark=Benchmark.LONGMEMEVAL, model="echo")
        tr.add(TrajectoryStep(step=0, kind="retrieve", timestamp=10.0, retrieved=[
            RetrievedItem(item=_mk_item("g", 5.0, 1), score=s_gold, rank=0),
            RetrievedItem(item=_mk_item("n", 4.0, 1), score=s_noise, rank=1),
        ]))
        return tr

    # Jaccard-era tiny scores and BM25-magnitude scores give the SAME precision.
    _, prec_small = M.relevancy([_two_item_traj(0.007, 0.005)], [task])
    _, prec_big = M.relevancy([_two_item_traj(3.2, 0.4)], [task])
    assert _approx(prec_small, 0.5), prec_small
    assert _approx(prec_big, 0.5), prec_big
    # is_gold gets annotated as a side effect (idempotent with recency).
    tr = _two_item_traj(3.2, 0.4)
    M.relevancy([tr], [task])
    flags = {ri.item_id: ri.is_gold for ri in tr.steps[0].retrieved}
    assert flags == {"g": True, "n": False}


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
        # Dataset accounting is reported for every run: used / available / limit.
        assert rec["entries_used"] == rr.n_tasks
        assert rec["entries_available"] == rr.metadata.get("total_available")
        assert rec["limit"] is None  # no --limit applied above
        # Appends accumulate; file is valid JSON with a runs[] list.
        append_result(rr, ledger)
        data = load_results(ledger)
        assert len(data["runs"]) == 2 and data["schema"] >= 1
        json.dumps(data)  # serializable


def test_results_report_dataset_entries_with_limit() -> None:
    """With --limit applied, the record reports used<available and the limit value."""
    from memeval.results import result_record
    from memeval.harness import run
    fx = _fixture("longmemeval.json")
    rr = run(Benchmark.LONGMEMEVAL, EchoModel(), True, path_or_id=fx, limit=1)
    rec = result_record(rr, run_id="lim")
    assert rec["entries_used"] == 1
    assert rec["entries_available"] >= 2  # fixture holds at least 2 entries
    assert rec["entries_used"] < rec["entries_available"]
    assert rec["limit"] == 1


def test_results_per_benchmark_versioned_files() -> None:
    """Per-benchmark results land at results/v{X.Y}/{bench}-{ts}.json with both runs."""
    from memeval.results import (
        normalize_version, run_timestamp, benchmark_results_path,
        write_benchmark_results, result_record,
    )
    from memeval.harness import run
    # version normalization accepts bare or v-prefixed forms
    assert normalize_version("0.1") == "v0.1"
    assert normalize_version("v0.2") == "v0.2"
    # timestamp is filesystem-safe (no ':')
    ts = run_timestamp()
    assert ts.endswith("Z") and ":" not in ts
    fx = _fixture("longmemeval.json")
    recs = [result_record(run(Benchmark.LONGMEMEVAL, EchoModel(), m, path_or_id=fx, limit=1),
                           run_id=f"m{int(m)}") for m in (True, False)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_benchmark_results("longmemeval", recs, version="0.1", timestamp=ts, root=tmp)
        # path shape: <root>/v0.1/longmemeval-<ts>.json
        assert path == benchmark_results_path("longmemeval", version="0.1", timestamp=ts, root=tmp)
        assert path.parent.name == "v0.1" and path.name == f"longmemeval-{ts}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["benchmark"] == "longmemeval" and doc["memory_version"] == "v0.1"
        assert doc["timestamp"] == ts and len(doc["runs"]) == 2  # both modes in one file


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
# ClaudeCodeAgent CODE-task diff emission (offline; no claude, no net)
# --------------------------------------------------------------------------- #
def test_extract_diff_plain_passthrough() -> None:
    # Output that already IS a clean git diff comes back essentially unchanged,
    # anchored at 'diff --git', with the hunk body preserved + a trailing newline.
    from memeval.claudecode.agent import _extract_diff
    diff = (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new"
    )
    out = _extract_diff(diff)
    assert out.startswith("diff --git a/f.py b/f.py")
    assert "@@ -1 +1 @@" in out and "-old" in out and "+new" in out
    assert out.endswith("\n")            # git apply wants a final newline


def test_extract_diff_strips_fences() -> None:
    # A ```diff ... ``` fence (and a bare ``` ... ``` fence) yields the inner diff
    # with NO backtick fence lines left in the output.
    from memeval.claudecode.agent import _extract_diff
    inner = (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    fenced = "```diff\n" + inner + "```\n"
    out = _extract_diff(fenced)
    assert out.startswith("diff --git a/f.py b/f.py")
    assert "```" not in out
    # bare fence (no language tag) is handled the same way
    bare = "```\n" + inner + "```"
    out2 = _extract_diff(bare)
    assert out2.startswith("diff --git a/f.py b/f.py")
    assert "```" not in out2
    # a MISLABELED language tag (```python around a diff) is still bounded by the
    # fence body, so the closing fence and any trailing prose never leak through.
    mislabeled = "```python\n" + inner + "```\nLet me know if this helps!\n"
    out3 = _extract_diff(mislabeled)
    assert out3.startswith("diff --git a/f.py b/f.py")
    assert "```" not in out3
    assert "Let me know" not in out3


def test_extract_diff_drops_prose_before_and_after() -> None:
    # Leading commentary and a trailing "anything else?" note are both stripped;
    # only the diff survives.
    from memeval.claudecode.agent import _extract_diff
    text = (
        "Here is the fix:\n"
        "\n"
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "\n"
        "Let me know if this helps."
    )
    out = _extract_diff(text)
    assert out.startswith("diff --git a/f.py b/f.py")
    assert "Here is the fix" not in out      # leading prose dropped
    assert "Let me know" not in out          # trailing prose dropped
    assert "+new" in out


def test_extract_diff_multiple_files_preserved() -> None:
    # Two consecutive 'diff --git' sections (two files) both survive.
    from memeval.claudecode.agent import _extract_diff
    text = (
        "diff --git a/one.py b/one.py\n"
        "--- a/one.py\n"
        "+++ b/one.py\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
        "diff --git a/two.py b/two.py\n"
        "--- a/two.py\n"
        "+++ b/two.py\n"
        "@@ -1 +1 @@\n"
        "-c\n"
        "+d\n"
    )
    out = _extract_diff(text)
    assert out.startswith("diff --git a/one.py b/one.py")
    assert out.count("diff --git ") == 2     # both file sections preserved


def test_extract_diff_empty_and_no_diff_returns_empty() -> None:
    # Empty / whitespace / pure-prose (refusal) input never yields prose — it
    # yields '' so the grader records an honest empty patch, never a git-apply of
    # free text.
    from memeval.claudecode.agent import _extract_diff
    assert _extract_diff("") == ""
    assert _extract_diff("   \n \t\n ") == ""
    assert _extract_diff("I cannot solve this issue.") == ""


def test_extract_diff_bare_unified_diff_fallback() -> None:
    # A plain unified diff lacking the 'diff --git' header is recognized via the
    # '--- ' + ('+++ '|'@@ ') fallback; a lone '--- ' prose line is NOT.
    from memeval.claudecode.agent import _extract_diff
    bare = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n"
    out = _extract_diff(bare)
    assert out.startswith("--- a/f.py")
    assert "@@ -1 +1 @@" in out and "+b" in out
    # a dash line that is NOT a diff yields '' (no following +++/@@).
    assert _extract_diff("--- not a diff, just a list item") == ""


def test_build_code_prompt_includes_repo_and_instruction() -> None:
    # _build_code_prompt surfaces the issue text, repo/base-commit context (when
    # present), and the strict diff-only instruction; missing fields don't crash.
    from memeval.claudecode.agent import _build_code_prompt
    t = Task(
        task_id="c1", benchmark=Benchmark.SWE_CONTEXTBENCH, kind=TaskKind.CODE,
        question="Fix the crash on empty input.",
        repo="example/repo", base_commit="deadbeef",
    )
    p = _build_code_prompt(t)
    assert "Fix the crash on empty input." in p
    assert "Repository: example/repo" in p
    assert "Base commit: deadbeef" in p
    assert "unified diff" in p
    # No repo/base_commit -> those lines omitted, but question + instruction stay.
    t2 = Task(task_id="c2", benchmark=Benchmark.SWE_CONTEXTBENCH, kind=TaskKind.CODE,
              question="Only a question here.")
    p2 = _build_code_prompt(t2)
    assert "Only a question here." in p2
    assert "Repository:" not in p2 and "Base commit:" not in p2
    assert "unified diff" in p2


def test_claudecode_solve_code_emits_extracted_diff() -> None:
    # End-to-end via run_agent: a CODE benchmark + a fake runner returning prose-
    # wrapped fenced diff. The graded prediction must be the EXTRACTED diff (starts
    # with 'diff --git', no fences), and the runner must be asked for a diff.
    from memeval.claudecode.agent import ClaudeCodeAgent, _SYS_CODE
    from memeval.claudecode.cli import ClaudeResult
    seen: dict = {}

    def fake(prompt, *, cwd, mcp_config=None, allowed_tools=None,
             append_system_prompt=None, **kw):
        seen["prompt"] = prompt
        seen["system"] = append_system_prompt
        seen["mcp"] = mcp_config
        seen["tools"] = allowed_tools
        return ClaudeResult(
            text=(
                "Sure!\n"
                "```diff\n"
                "diff --git a/f.py b/f.py\n"
                "--- a/f.py\n"
                "+++ b/f.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
                "```"
            ),
            tokens_in=30, tokens_out=8,
        )

    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="off", runner=fake, workdir=tmp)
        rr = run_agent(Benchmark.SWE_CONTEXTBENCH, agent, memory=False,
                       path_or_id=_fixture("swe_contextbench.json"), limit=1,
                       seed_sessions=False)
    assert rr.n_tasks == 1
    pred = rr.trajectories[0].prediction
    assert pred.startswith("diff --git a/f.py b/f.py")   # EXTRACTED, not the prose
    assert "```" not in pred and "Sure!" not in pred      # fences/prose stripped
    assert seen["system"] == _SYS_CODE                    # CODE system prompt used
    assert "unified diff" in seen["prompt"]               # prompt requests a diff
    assert seen["mcp"] is None and seen["tools"] is None  # plain turn, no MCP


def test_claudecode_solve_qa_unchanged() -> None:
    # Regression guard: a QA benchmark takes the EXACT existing path — verbatim
    # res.text prediction (NOT diff-extracted) and the plain QA system prompt.
    # Proves _SYS_CODE / _extract_diff are never reached for QA.
    from memeval.claudecode.agent import ClaudeCodeAgent, _SYS_PLAIN
    from memeval.claudecode.cli import ClaudeResult
    seen: dict = {}

    def fake(prompt, *, cwd, mcp_config=None, allowed_tools=None,
             append_system_prompt=None, **kw):
        seen["system"] = append_system_prompt
        return ClaudeResult(text="Berlin", tokens_in=12, tokens_out=2)

    with tempfile.TemporaryDirectory() as tmp:
        agent = ClaudeCodeAgent(memory_mode="off", runner=fake, workdir=tmp)
        rr = run_agent(Benchmark.MEMORY_AGENT_BENCH, agent, memory=False,
                       path_or_id=_fixture("memoryagentbench.json"), limit=1,
                       seed_sessions=False)
    assert rr.n_tasks == 1
    assert rr.trajectories[0].prediction == "Berlin"     # verbatim, NOT a diff
    assert seen["system"] == _SYS_PLAIN                   # QA path untouched


def test_plugin_real_openrouter_advisory_is_nonfatal() -> None:
    """plugin-real must NOT depend on OPENROUTER_API_KEY.

    With the key unset the run still proceeds (memory is seeded via the plugin's
    own memory-cli; the dream/Daydreamer consolidation is a no-op, ADR-dreaming-012)
    — only a NON-fatal advisory is emitted. This keeps the empty-memory -> dream
    inserts -> re-run -> compare workflow runnable.
    """
    from memeval.claudecode import run_bench

    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        note = run_bench._openrouter_advisory(["plugin-real"])
        assert note is not None and "OPENROUTER_API_KEY" in note   # advisory present
        # non-plugin-real modes are unaffected
        assert run_bench._openrouter_advisory(["builtin", "plugin"]) is None
        # key present -> no advisory
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        assert run_bench._openrouter_advisory(["plugin-real"]) is None
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved


def test_run_bench_default_modes_are_builtin_and_plugin_real() -> None:
    """The default `all` comparison is Claude Code native (builtin) vs Keith's
    SHIPPING plugin (plugin-real). The OKF `plugin` simulation must NOT be in `all`
    (it stays explicit opt-in), so it is never benchmarked by accident in place of
    the real product."""
    import tempfile
    from pathlib import Path
    from memeval.claudecode import run_bench

    # The canonical "all" set.
    assert run_bench._MODES == ["builtin", "plugin-real"]
    assert "plugin" not in run_bench._MODES

    # `--mode all` (the default) expands to exactly those modes. Stub _run_one so
    # main() captures the swept modes without running a real benchmark.
    swept: list[str] = []
    orig = run_bench._run_one

    def _stub(benchmark, mode, args, **kw):
        swept.append(mode)
        return None

    run_bench._run_one = _stub  # type: ignore[assignment]
    try:
        run_bench.main(["--benchmark", "swe_contextbench", "--results-dir", "",
                        "--results",
                        str(Path(tempfile.gettempdir()) / "memeval-rb-modes.json")])
    finally:
        run_bench._run_one = orig  # type: ignore[assignment]
    assert swept == ["builtin", "plugin-real"]

    # The OKF simulation remains selectable as an EXPLICIT single mode.
    swept.clear()
    run_bench._run_one = _stub  # type: ignore[assignment]
    try:
        run_bench.main(["--benchmark", "swe_contextbench", "--mode", "plugin",
                        "--results-dir", "", "--results",
                        str(Path(tempfile.gettempdir()) / "memeval-rb-modes.json")])
    finally:
        run_bench._run_one = orig  # type: ignore[assignment]
    assert swept == ["plugin"]


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
