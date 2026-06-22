"""Shared plumbing for native evaluators (optional base class + helpers).

Per-benchmark evaluators do NOT have to subclass anything — satisfying the
:class:`memeval.native.spec.NativeEvaluator` Protocol is enough. But every
evaluator needs the same few things, so :class:`BaseNativeEvaluator` collects
them in one place:

* :meth:`BaseNativeEvaluator.run_tasks` — the canonical way to get one
  :class:`~memeval.schema.Trajectory` (with ``prediction`` + ``success``) per
  task, **offline and deterministic**, by reusing the EXISTING multi-step
  :func:`memeval.agent.run_agent` over an :class:`memeval.agent.EchoAgent` and a
  per-task/per-group :class:`memeval.harness.InMemoryStore`. It returns
  :class:`~memeval.native.spec.PerTaskRecord` objects ready for ``score``.
* :meth:`mode_to_memory` — map a mode string to the ``memory`` bool the harness
  expects.
* set-overlap + token-overlap helpers used by the retrieval/code benchmarks.

Everything is standard-library only. Heavy paths (a real AgentAdapter, the
SWE-bench Docker grader) are reached only when the caller passes them in.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional, Sequence

from ...agent import AgentAdapter, EchoAgent
from ...grader import overlap_grader, resolved_from_report
from ...protocols import MemoryStore
from ...schema import Task, Trajectory
from ..spec import BenchmarkNativeReport, ComponentScore, NativeMetric, PerTaskRecord


#: Modes that mean "consult memory". "off" is the only memory-OFF mode.
_MEMORY_ON_MODES = {"builtin", "plugin", "plugin-real", "echo", "on", "memory"}


def mode_to_memory(mode: str) -> bool:
    """Map a mode string to the harness ``memory`` flag.

    ``"off"`` (and ``"no-memory"`` / ``"baseline"``) -> ``False``; everything
    else (``"builtin"`` / ``"plugin"`` / ``"plugin-real"`` / ``"echo"``) ->
    ``True``. The native evaluators only need the on/off bit here; which concrete
    memory MECHANISM runs is decided by the agent passed to ``run`` (EchoAgent
    offline; ClaudeCodeAgent online), exactly as in the existing harness.
    """
    return (mode or "off").strip().lower() in _MEMORY_ON_MODES


class BaseNativeEvaluator:
    """Optional base with the common run-and-score plumbing.

    Subclasses set :attr:`benchmark` (a :class:`~memeval.schema.Benchmark` value
    string) and implement :meth:`score`; many can use :meth:`run_tasks` verbatim
    for :meth:`NativeEvaluator.run`. Subclasses needing a bespoke run protocol
    (MemoryAgentBench's chunk-ingest stream, SWE-Bench-CL's three-vector
    continual passes, SWE-ContextBench's A/B configs) override :meth:`run` and
    can still call :meth:`run_tasks` for each pass.
    """

    #: Benchmark value string this evaluator scores (override in subclass).
    benchmark: str = ""

    # ------------------------------------------------------------------ #
    # Canonical offline run: one Trajectory (+prediction+success) per task
    # ------------------------------------------------------------------ #
    def run_tasks(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        memory: bool = True,
        store: Optional[MemoryStore] = None,
        cost: Any = None,
        grader: Optional[Callable[[Task, str], Optional[bool]]] = None,
        k: int = 5,
        group_aware: bool = False,
        **run_kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Run ``tasks`` once and return one :class:`PerTaskRecord` per task.

        This is THE reuse helper. It reuses the EXISTING, frozen agent seam —
        :class:`memeval.agent.AgentContext` + the agent's ``solve`` + the
        harness's per-group :class:`memeval.harness.InMemoryStore` policy — but
        drives the EXACT ``tasks`` list it was handed (see :meth:`_run_exact`).
        We intentionally do NOT call :func:`memeval.agent.run_agent` directly,
        because that function re-loads tasks from a source path; the runner has
        already loaded + limited (and a subclass may have re-chunked sessions),
        so we must score that precise in-memory list.

        When ``agent_or_model`` is ``None`` or a plain
        :class:`~memeval.protocols.ModelAdapter` (e.g.
        :class:`~memeval.models.EchoModel`), it is wrapped in an
        :class:`~memeval.agent.EchoAgent` so the offline path needs no extra
        wiring. Trajectories come back fully populated (retrieve steps with
        ranked :class:`~memeval.schema.RetrievedItem`, ``prediction``,
        ``success``, tokens), with ``is_gold`` annotated. Records preserve input
        task order.

        ``group_aware`` and extra ``run_kwargs`` are accepted for signature
        symmetry with :func:`memeval.agent.run_agent`; per-group memory carry-
        over is already handled by the harness store policy in :meth:`_run_exact`.
        """
        agent = self._as_agent(agent_or_model)
        return self._run_exact(
            tasks, agent=agent, memory=memory, store=store, cost=cost,
            grader=grader, k=k,
        )

    def _run_exact(
        self,
        tasks: Sequence[Task],
        *,
        agent: AgentAdapter,
        memory: bool,
        store: Optional[MemoryStore],
        cost: Any,
        grader: Optional[Callable[[Task, str], Optional[bool]]],
        k: int,
    ) -> list[PerTaskRecord]:
        """Drive the EXACT ``tasks`` (no re-load) through the agent seam.

        Reuses :class:`memeval.agent.AgentContext` + the agent's ``solve`` and
        the harness's per-group store policy, but over the in-memory task list we
        were given (so a subclass that re-chunked sessions, or the runner's
        limit, is honored verbatim). Mirrors ``run_agent``'s per-task body.
        """
        from ...agent import (
            AgentContext, _agent_model, _coerce_result, _grade,
            _store_for_task, _task_query_time,
        )
        from ...metrics import _annotate_gold
        from ...schema import MemoryItem

        group_stores: dict[str, MemoryStore] = {}
        records: list[PerTaskRecord] = []
        for task in tasks:
            query_time = _task_query_time(task)
            step_ts = query_time if query_time is not None else 0.0
            traj = Trajectory(
                task_id=task.task_id, benchmark=task.benchmark, model=agent.name,
                memory_on=memory, started_at=0.0,
            )
            active_store = _store_for_task(task, store, group_stores)
            if memory:
                for sess in task.sessions:
                    active_store.write(MemoryItem.from_session(sess))
            ctx = AgentContext(
                task=task, store=active_store, model=_agent_model(agent),
                memory_on=memory, trajectory=traj, cost=cost, k=k,
                as_of=query_time, step_ts=step_ts,
            )
            result = agent.solve(task, ctx)
            pred, forced = _coerce_result(result)
            traj.prediction = pred
            traj.success = forced if forced is not None else _grade(task, pred, grader)
            traj.ended_at = 0.0
            _annotate_gold(traj, set(task.gold_memory_ids))
            records.append(PerTaskRecord.from_trajectory(traj))
        return records

    @staticmethod
    def _as_agent(agent_or_model: Any) -> AgentAdapter:
        """Coerce a model / None / agent into an :class:`AgentAdapter`.

        ``None`` -> :class:`EchoAgent` over :class:`EchoModel`. A bare
        :class:`~memeval.protocols.ModelAdapter` (has ``generate``, no
        ``solve``) is wrapped in :class:`EchoAgent`. An object that already has
        ``solve`` is returned as-is.
        """
        if agent_or_model is None:
            return EchoAgent()
        if hasattr(agent_or_model, "solve"):
            return agent_or_model  # already an AgentAdapter
        if hasattr(agent_or_model, "generate"):
            return EchoAgent(model=agent_or_model)
        return EchoAgent()

    # ------------------------------------------------------------------ #
    # Report skeleton
    # ------------------------------------------------------------------ #
    def empty_report(self, mode: str, n_tasks: int, **metadata: Any) -> BenchmarkNativeReport:
        """A blank :class:`BenchmarkNativeReport` for this benchmark + mode."""
        return BenchmarkNativeReport(
            benchmark=self.benchmark, mode=mode, n_tasks=n_tasks, metadata=dict(metadata),
        )


# --------------------------------------------------------------------------- #
# Stateless scoring helpers (shared across evaluators)
# --------------------------------------------------------------------------- #
def mean(values: Sequence[float]) -> float:
    """Arithmetic mean, or ``0.0`` for an empty sequence."""
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def set_recall(predicted: set, gold: set) -> float:
    """|predicted ∩ gold| / |gold|; ``0.0`` when ``gold`` is empty."""
    if not gold:
        return 0.0
    return len(predicted & gold) / len(gold)


def set_precision(predicted: set, gold: set) -> float:
    """|predicted ∩ gold| / |predicted|; ``0.0`` when ``predicted`` is empty."""
    if not predicted:
        return 0.0
    return len(predicted & gold) / len(predicted)


def f1(precision: float, recall: float) -> float:
    """Harmonic mean of precision and recall; ``0.0`` when both are ``0``."""
    denom = precision + recall
    return (2.0 * precision * recall / denom) if denom > 0.0 else 0.0


def token_overlap(pred: str, gold: str) -> float:
    """Jaccard token overlap of two strings (|a∩b| / |a∪b|)."""
    a = set((pred or "").split())
    b = set((gold or "").split())
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def ndcg_at_k(relevances: Sequence[float], k: int) -> float:
    """Binary/graded NDCG@k over a ranked relevance list (best-first).

    ``relevances[i]`` is the relevance of the rank-``i`` item. DCG uses the
    standard ``rel / log2(rank+2)`` discount; IDCG sorts the same relevances
    descending. Returns ``0.0`` when there is no positive relevance. Used by the
    LongMemEval session-recall ``ndcg_any@k`` metric.
    """
    rels = list(relevances)[: max(0, k)]
    if not rels:
        return 0.0
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
    ideal = sorted(list(relevances), reverse=True)[: max(0, k)]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return (dcg / idcg) if idcg > 0.0 else 0.0


def group_by_competency(records: Sequence[PerTaskRecord], tasks: Sequence[Task]) -> dict[str, list[PerTaskRecord]]:
    """Group records by their task's :meth:`Task.stratum` (competency or kind)."""
    by_id = {t.task_id: t for t in tasks}
    out: dict[str, list[PerTaskRecord]] = {}
    for r in records:
        t = by_id.get(r.task_id)
        key = t.stratum() if t is not None else "unknown"
        out.setdefault(key, []).append(r)
    return out


def expand_line_set(file: str, start: int, end: int) -> set[tuple[str, int]]:
    """``{(file, line) for line in [start, end]}`` — line-granularity set.

    Inclusive of both endpoints; swaps if ``start > end``; a single missing/zero
    bound degrades to the present one. Used by ContextBench / SWE-ContextBench
    line-level recall/precision.
    """
    if not file:
        return set()
    lo, hi = (start, end) if start <= end else (end, start)
    if lo <= 0 and hi <= 0:
        return set()
    lo = max(1, lo)
    return {(file, ln) for ln in range(lo, hi + 1)}


def retrieved_item_ids(traj: Trajectory, *, last_only: bool = False) -> list[str]:
    """Item ids retrieved across a trajectory's retrieve steps (ranked order).

    With ``last_only`` return only the FINAL retrieve step's ids (the "declared"
    set ContextBench scores recall/precision against); otherwise the cumulative
    union across all retrieve steps, de-duplicated keeping first-seen order.
    """
    steps = [s for s in traj.steps if s.kind == "retrieve"]
    if not steps:
        return []
    if last_only:
        return [ri.item_id for ri in steps[-1].retrieved]
    seen: set[str] = set()
    out: list[str] = []
    for s in steps:
        for ri in s.retrieved:
            if ri.item_id not in seen:
                seen.add(ri.item_id)
                out.append(ri.item_id)
    return out


__all__ = [
    "BaseNativeEvaluator",
    "mode_to_memory",
    "mean",
    "set_recall",
    "set_precision",
    "f1",
    "token_overlap",
    "ndcg_at_k",
    "group_by_competency",
    "expand_line_set",
    "retrieved_item_ids",
    # re-exports for convenience in evaluator modules
    "NativeMetric",
    "ComponentScore",
    "BenchmarkNativeReport",
    "PerTaskRecord",
    "overlap_grader",
    "resolved_from_report",
]
