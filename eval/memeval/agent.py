"""Multi-step **agent** seam — drive a real agent (e.g. OpenCode) through a benchmark.

`memeval.harness.run` is a *single-shot* loop (one retrieve → one generate) that
is perfect for the QA-style memory benchmarks. Coding agents are different: they
run a **multi-step loop** (retrieve → generate → run a tool → write a memory →
retrieve again → …). This module adds that path **without touching the frozen
contract** (`schema.py` / `protocols.py`) or Keith's `harness.py`:

* :class:`AgentAdapter` — the structural seam an agent implements (one method,
  ``solve(task, ctx)``). **OpenCode plugs in here**: Keith wraps the OpenCode
  loop as an ``AgentAdapter`` and, on each step, calls ``ctx.retrieve`` /
  ``ctx.remember`` against the **shared** :class:`MemoryStore` (his real memory
  harness passed in as ``store=``), and reports generations via ``ctx.generate``
  (or ``ctx.record_generate`` if it called its own model).
* :class:`AgentContext` — what the agent is handed each task: the shared store,
  a model, and recorders that keep **cost + trajectory + grading centralized**
  here while the agent owns the loop logic. Every memory op and generation it
  makes lands as a :class:`TrajectoryStep`, so the existing metrics and the
  dreaming worker consume agent runs unchanged.
* :func:`run_agent` — the sibling of ``harness.run`` that drives an
  ``AgentAdapter`` over a benchmark and returns the same :class:`RunResult`.
* :class:`EchoAgent` / :func:`function_agent` — offline reference agents (a real
  3-step retrieve→generate→write loop) so the seam is exercised with zero deps.

Standard-library only; the offline path needs nothing extra.

Example (offline)::

    from memeval.agent import run_agent, EchoAgent
    from memeval.schema import Benchmark
    rr = run_agent(Benchmark.LONGMEMEVAL, EchoAgent(), memory=True,
                   path_or_id="tests/fixtures/longmemeval.json")

How OpenCode plugs in (architecture A)::

    class OpenCodeAgent:               # satisfies AgentAdapter
        name = "opencode+haiku"; price_in = ...; price_out = ...
        def solve(self, task, ctx):
            while not done:
                hits = ctx.retrieve(current_query)      # -> Keith's memory harness
                out  = ctx.generate(build_prompt(hits)) # charged + logged
                ctx.remember(learned_fact)              # written to the shared store
            return AgentResult(prediction=patch, patch=patch)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Union, runtime_checkable

from .cost import BudgetExceeded, CostTracker
from .harness import InMemoryStore, stratified_dev_slice
from .loaders import get_loader
from .metrics import compute_metrics, qa_match
from .models import EchoModel, estimate_tokens_words
from . import tracing
from .protocols import MemoryStore, ModelAdapter
from .schema import (
    Benchmark,
    MemoryItem,
    Metrics,
    ModelConfig,
    RetrievedItem,
    RunResult,
    Session,
    Task,
    TaskKind,
    Trajectory,
    TrajectoryStep,
)
from .trajectory import TrajectoryLogger

_UNSET = object()


# --------------------------------------------------------------------------- #
# Result returned by an agent's solve()
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class AgentResult:
    """What an :class:`AgentAdapter` may return from ``solve``.

    Return a bare ``str`` for the simple case (it becomes ``prediction``); return
    an :class:`AgentResult` when the agent produces a code ``patch`` and/or has
    already decided ``success`` (e.g. it ran the tests itself). ``success``
    overrides the harness grader when not ``None``.
    """

    prediction: str = ""
    patch: Optional[str] = None
    success: Optional[bool] = None
    metadata: dict[str, Any] = field(default_factory=dict)


SolveReturn = Union[str, AgentResult]


# --------------------------------------------------------------------------- #
# AgentAdapter protocol — the seam OpenCode implements
# --------------------------------------------------------------------------- #
@runtime_checkable
class AgentAdapter(Protocol):
    """A multi-step agent behind a uniform entry point.

    Unlike :class:`~memeval.protocols.ModelAdapter` (one ``generate`` call), an
    agent runs its **own loop** inside ``solve``, using the :class:`AgentContext`
    to retrieve from / write to the shared memory store and to generate. The
    ``name`` / ``price_in`` / ``price_out`` attributes mirror ``ModelAdapter`` so
    a :class:`~memeval.schema.ModelConfig` can be built for the results grid.
    """

    name: str
    price_in: float
    price_out: float

    def solve(self, task: Task, ctx: "AgentContext", **kwargs: Any) -> SolveReturn:
        """Solve ``task`` using ``ctx`` (retrieve / generate / remember). Return
        the prediction string or an :class:`AgentResult`."""
        ...


# --------------------------------------------------------------------------- #
# AgentContext — handed to the agent each task; centralizes cost+trajectory
# --------------------------------------------------------------------------- #
class AgentContext:
    """Per-task toolbox the agent uses; records every step + charges cost.

    The agent calls these so the harness stays the single source of truth for
    the :class:`Trajectory`, the :class:`CostTracker`, and budget enforcement —
    the agent only decides *what* to do, not *how it's measured*.

    Memory methods (no-op-safe when ``memory_on`` is False so the same agent code
    runs the memory-off baseline):

    * :meth:`retrieve` — search the shared store, log a ``retrieve`` step, return hits.
    * :meth:`generate` — call the bound model, charge cost, log a ``generate`` step.
    * :meth:`remember` / :meth:`remember_item` — write to the shared store, log a ``write`` step.
    * :meth:`record_retrieve` / :meth:`record_generate` — for agents (OpenCode)
      that did the retrieval/generation themselves and just report it.
    * :meth:`note` — log a free-text ``note`` step.
    """

    def __init__(
        self,
        *,
        task: Task,
        store: MemoryStore,
        model: ModelAdapter,
        memory_on: bool,
        trajectory: Trajectory,
        cost: Optional[CostTracker],
        k: int,
        as_of: Optional[float],
        step_ts: float,
        tracer: Any = None,
    ) -> None:
        self.task = task
        self.store = store
        self.model = model
        self.memory_on = memory_on
        self.k = k
        self.as_of = as_of
        self._traj = trajectory
        self._cost = cost
        self._ts = step_ts
        self._tracer = tracer if tracer is not None else tracing.NOOP

    # -- retrieval -------------------------------------------------------- #
    def retrieve(
        self,
        query: Optional[str] = None,
        *,
        k: Optional[int] = None,
        as_of: Any = _UNSET,
    ) -> list[RetrievedItem]:
        """Search the shared store and log a ``retrieve`` step. Empty if memory off."""
        if not self.memory_on:
            return []
        q = self.task.question if query is None else query
        kk = self.k if k is None else k
        ao = self.as_of if as_of is _UNSET else as_of
        hits = self.store.search(q, k=kk, as_of=ao)
        self.record_retrieve(hits, query=q)
        return hits

    def record_retrieve(self, hits: list[RetrievedItem], *, query: str = "") -> None:
        """Log a ``retrieve`` step for hits the agent fetched itself."""
        self._traj.add(TrajectoryStep(
            step=0, kind="retrieve", content=query, timestamp=self._ts,
            retrieved=list(hits),
        ))
        self._tracer.step(
            "retrieve", "retrieve", input=query,
            metadata={"k": len(hits), "hits": [
                {"id": h.item_id, "score": round(h.score, 4), "rank": h.rank}
                for h in hits
            ]},
        )

    # -- generation ------------------------------------------------------- #
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Call the bound model, charge cost, log a ``generate`` step; return text.

        Raises :class:`BudgetExceeded` (propagated to :func:`run_agent`) before
        the step is recorded, so a budget breach never half-commits a step.
        """
        text, tin, tout = self.model.generate(prompt, **kwargs)
        self.record_generate(text, tin, tout, prompt=prompt)
        return text

    def record_generate(
        self,
        text: str,
        tokens_in: int,
        tokens_out: int,
        *,
        prompt: str = "",
        model_name: Optional[str] = None,
    ) -> None:
        """Charge cost and log a ``generate`` step for an externally-run model.

        OpenCode (which calls its own LLM) uses this to report each turn's tokens
        so cost + the efficiency metric stay accurate. Charges BEFORE logging so
        a :class:`BudgetExceeded` aborts cleanly.
        """
        if self._cost is not None:
            self._cost.add(model_name or self.model.name, tokens_in, tokens_out)
        self._traj.add(TrajectoryStep(
            step=0, kind="generate", content=text, timestamp=self._ts,
            tokens_in=tokens_in, tokens_out=tokens_out,
        ))
        self._tracer.step(
            "generate", "generate", output=text,
            tokens_in=tokens_in, tokens_out=tokens_out,
        )

    # -- memory writes ---------------------------------------------------- #
    def remember(self, content: str, *, item_id: Optional[str] = None,
                 relevancy: float = 1.0, tags: Optional[list[str]] = None,
                 timestamp: Optional[float] = None) -> Optional[str]:
        """Write a new memory from ``content`` and log a ``write`` step.

        No-op (returns ``None``) when memory is off. The id defaults to a stable
        per-task counter so repeated writes are distinct and deterministic.
        """
        if not self.memory_on:
            return None
        n = sum(1 for s in self._traj.steps if s.kind == "write")
        iid = item_id or f"{self.task.task_id}::mem{n}"
        item = MemoryItem(
            item_id=iid, content=content,
            timestamp=self._ts if timestamp is None else timestamp,
            relevancy=relevancy, source="agent", tags=list(tags or []),
            tokens=estimate_tokens_words(content),
        )
        return self.remember_item(item)

    def remember_item(self, item: MemoryItem) -> Optional[str]:
        """Write a prepared :class:`MemoryItem` and log a ``write`` step."""
        if not self.memory_on:
            return None
        self.store.write(item)
        self._traj.add(TrajectoryStep(
            step=0, kind="write", content=item.content, timestamp=self._ts,
            metadata={"item_id": item.item_id},
        ))
        self._tracer.step(
            "write", "write", input=item.content,
            metadata={"item_id": item.item_id},
        )
        return item.item_id

    def note(self, text: str) -> None:
        """Log a free-text ``note`` step (e.g. a tool call / reasoning marker)."""
        self._traj.add(TrajectoryStep(
            step=0, kind="note", content=text, timestamp=self._ts,
        ))
        self._tracer.step("note", "note", input=text)


# --------------------------------------------------------------------------- #
# run_agent — drive an AgentAdapter over a benchmark (sibling of harness.run)
# --------------------------------------------------------------------------- #
def run_agent(
    benchmark: "Benchmark | str",
    agent: AgentAdapter,
    memory: bool,
    *,
    store: Optional[MemoryStore] = None,
    limit: Optional[int] = None,
    dev_slice: Optional[float | int] = None,
    path_or_id: Optional[str] = None,
    cost: Optional[CostTracker] = None,
    logger: Optional[TrajectoryLogger] = None,
    grader: Optional[Callable[[Task, str], Optional[bool]]] = None,
    config: Optional[ModelConfig] = None,
    group_aware: bool = False,
    k: int = 5,
    tau: float = 86400.0,
    threshold: float = 0.7,
    seed_sessions: bool = True,
    clock: Callable[[], float] = time.time,
) -> RunResult:
    """Run one benchmark through a multi-step :class:`AgentAdapter`.

    Mirrors :func:`memeval.harness.run` but delegates the per-task loop to
    ``agent.solve(task, ctx)``. ``store`` is the shared :class:`MemoryStore`
    (pass **Keith's memory harness** here for the real integration; defaults to
    a per-task / per-group :class:`InMemoryStore`). When ``memory`` and
    ``seed_sessions`` are both true, each task's sessions are written to the store
    before the agent runs (the context it retrieves from); the agent's own
    ``remember`` calls accumulate on top.

    Returns the same :class:`RunResult` shape as ``harness.run`` (aggregate
    metrics + per-task trajectories + cost), so dashboards/aggregation are identical.
    """
    bench = benchmark if isinstance(benchmark, Benchmark) else Benchmark.from_str(benchmark)
    cfg = config or ModelConfig(
        name=agent.name, memory=memory,
        price_in=getattr(agent, "price_in", 0.0),
        price_out=getattr(agent, "price_out", 0.0),
    )

    started_at = clock()
    loader = get_loader(bench)
    all_tasks = loader.load(path_or_id, limit=None)
    total_available = len(all_tasks)

    tasks = all_tasks
    truncated = False
    if dev_slice is not None:
        tasks = stratified_dev_slice(tasks, _as_fraction(dev_slice, len(tasks)))
        truncated = truncated or len(tasks) < total_available
    if limit is not None and len(tasks) > limit:
        truncated = True
        # Group-aware draw (for benchmarks whose memory lives *across* entries in a
        # group_id sequence): fill the limit with whole groups, largest first, so a
        # singleton-heavy dataset doesn't yield entries with no priors. Falls back
        # to a flat prefix when group_aware is off (or there's effectively 1 group).
        tasks = _select_group_aware(tasks, limit) if group_aware else tasks[:limit]

    trajectories: list[Trajectory] = []
    group_stores: dict[str, MemoryStore] = {}
    budget_hit = False

    for task in tasks:
        active_store = _store_for_task(task, store, group_stores)
        query_time = _task_query_time(task)
        step_ts = query_time if query_time is not None else 0.0

        traj = Trajectory(
            task_id=task.task_id, benchmark=task.benchmark, model=agent.name,
            memory_on=memory, started_at=clock(),
        )
        if memory and seed_sessions:
            for sess in task.sessions:
                active_store.write(MemoryItem.from_session(sess))

        with tracing.task_span(
            task.task_id, input=task.question,
            metadata={"benchmark": bench.value, "memory": memory, "agent": agent.name},
        ) as tspan:
            ctx = AgentContext(
                task=task, store=active_store, model=_agent_model(agent),
                memory_on=memory, trajectory=traj, cost=cost, k=k,
                as_of=query_time, step_ts=step_ts, tracer=tspan,
            )
            try:
                result = agent.solve(task, ctx)
            except BudgetExceeded:
                budget_hit = True
                break
            except Exception as exc:  # noqa: BLE001 - one task must not abort the run
                # A single task's failure (e.g. a flaky CLI/MCP call or a per-task
                # timeout) is recorded as a miss so the rest of the run still
                # produces metrics + a result file, instead of aborting everything.
                print(f"  task {task.task_id} failed ({type(exc).__name__}): "
                      f"{str(exc)[:160]}", flush=True)
                result = ""
            pred, forced_success = _coerce_result(result)
            tspan.update(output=pred)

        traj.prediction = pred
        traj.success = (
            forced_success if forced_success is not None
            else _grade(task, pred, grader)
        )
        traj.ended_at = clock()
        trajectories.append(traj)
        if logger is not None:
            logger.log(traj)

    ended_at = clock()
    metrics: Metrics = compute_metrics(
        trajectories, tasks[: len(trajectories)], tau=tau, threshold=threshold
    )
    tokens_in = sum(s.tokens_in for t in trajectories for s in t.steps)
    tokens_out = sum(s.tokens_out for t in trajectories for s in t.steps)
    partial = truncated or budget_hit or len(trajectories) < total_available

    # Mirror the aggregate metrics into Langfuse as run-level scores (no-op offline).
    with tracing.task_span(
        f"run:{bench.value}:{cfg.label}",
        metadata={"cost_usd": cost.spent_usd if cost is not None else 0.0,
                  "n_tasks": len(trajectories), "partial": partial},
    ) as rspan:
        for _m in ("recency", "efficiency", "relevancy", "accuracy"):
            rspan.score(_m, getattr(metrics, _m))
    tracing.flush()

    return RunResult(
        benchmark=bench, config=cfg, metrics=metrics, trajectories=trajectories,
        n_tasks=len(trajectories),
        cost_usd=cost.spent_usd if cost is not None else 0.0,
        tokens_in=tokens_in, tokens_out=tokens_out,
        budget_exceeded=budget_hit, partial=partial,
        started_at=started_at, ended_at=ended_at,
        metadata={
            "memory": memory, "k": k, "tau": tau, "threshold": threshold,
            "agent": agent.name, "mode": "agent",
            "total_available": total_available,
            "limit": limit,
            "select": "group" if group_aware else "flat",
            "source": path_or_id or getattr(loader, "default_source", ""),
        },
    )


# --------------------------------------------------------------------------- #
# Reference agents (offline)
# --------------------------------------------------------------------------- #
class EchoAgent:
    """Offline reference :class:`AgentAdapter` — a real 3-step loop.

    Per task it (1) **retrieves** the top-k from the shared store, (2)
    **generates** an answer with an :class:`EchoModel` over a memory-injected
    prompt, and (3) **writes back** what it learned (a Q→A memory) so a later
    task in the same ``group_id`` can retrieve it. That write-back is the thing
    single-shot ``harness.run`` does not do — it demonstrates the multi-step
    memory accumulation the real OpenCode agent will perform. Deterministic.
    """

    name = "echo-agent"
    price_in = 0.0
    price_out = 0.0

    def __init__(self, model: Optional[ModelAdapter] = None, *,
                 name: str = "echo-agent", write_back: bool = True) -> None:
        self.name = name
        self.model = model or EchoModel()
        self.write_back = write_back
        self.price_in = getattr(self.model, "price_in", 0.0)
        self.price_out = getattr(self.model, "price_out", 0.0)

    def solve(self, task: Task, ctx: AgentContext, **kwargs: Any) -> str:
        hits = ctx.retrieve(task.question)
        parts = [f"[memory] {h.item.content}" for h in sorted(hits, key=lambda x: x.rank, reverse=True)]
        parts.append(f"Question: {task.question}")
        if task.choices:
            parts.append("Choices: " + " | ".join(task.choices))
        parts.append("Answer:")
        text = ctx.generate("\n".join(parts))
        if self.write_back and text:
            ctx.remember(f"Q: {task.question} A: {text}", tags=["qa"])
        return text


def function_agent(
    fn: Callable[[Task, AgentContext], SolveReturn],
    *,
    name: str = "fn-agent",
    price_in: float = 0.0,
    price_out: float = 0.0,
) -> AgentAdapter:
    """Adapt a plain ``fn(task, ctx) -> str | AgentResult`` into an AgentAdapter."""

    class _FnAgent:
        def __init__(self) -> None:
            self.name = name
            self.price_in = price_in
            self.price_out = price_out

        def solve(self, task: Task, ctx: AgentContext, **kwargs: Any) -> SolveReturn:
            return fn(task, ctx)

    return _FnAgent()


# --------------------------------------------------------------------------- #
# Internal helpers (small, local — keep harness.py untouched)
# --------------------------------------------------------------------------- #
def _agent_model(agent: AgentAdapter) -> ModelAdapter:
    """The model an agent exposes for ``ctx.generate``; EchoModel as a fallback."""
    m = getattr(agent, "model", None)
    if m is not None and hasattr(m, "generate"):
        return m  # type: ignore[return-value]
    return EchoModel(name=getattr(agent, "name", "echo"))


def _coerce_result(result: SolveReturn) -> tuple[str, Optional[bool]]:
    """Normalize a solve() return into ``(prediction, forced_success)``."""
    if isinstance(result, AgentResult):
        pred = result.prediction or (result.patch or "")
        return pred, result.success
    return (result or ""), None


def _grade(task: Task, prediction: str,
           grader: Optional[Callable[[Task, str], Optional[bool]]]) -> Optional[bool]:
    """QA -> normalized exact match; CODE -> external grader or None (ungraded)."""
    if grader is not None:
        return grader(task, prediction)
    if task.kind == TaskKind.QA and task.answer is not None:
        return qa_match(prediction, task.answer)
    return None


def _select_group_aware(tasks: list[Task], limit: int) -> list[Task]:
    """Pick ``limit`` tasks by **whole group_id groups, largest first**.

    Memory in the continual-learning benches lives *across* the entries of a
    ``group_id`` sequence, so a flat prefix of a singleton-heavy dataset yields
    entries with no priors. Taking the largest groups first maximizes the depth
    of accumulated priors (the point of a *long*-memory test) and naturally skips
    singletons. Order within a group is preserved (priors precede dependents); the
    final group is taken as a prefix to land exactly on ``limit``. Ungrouped tasks
    are each treated as their own group (so this degrades to a flat prefix when
    there is effectively one group or all groups are singletons).
    """
    groups: dict[str, list[Task]] = {}
    order: list[str] = []
    for t in tasks:
        key = t.group_id or f"\0{t.task_id}"  # ungrouped -> unique singleton key
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    # Largest groups first; stable tie-break by first appearance for determinism.
    ranked = sorted(order, key=lambda key: (-len(groups[key]), order.index(key)))
    selected: list[Task] = []
    for key in ranked:
        if len(selected) >= limit:
            break
        selected.extend(groups[key][: limit - len(selected)])
    return selected


def _store_for_task(task: Task, store: Optional[MemoryStore],
                    group_stores: dict[str, MemoryStore]) -> MemoryStore:
    """Caller store wins; else per-group store (continual carry-over); else fresh."""
    if store is not None:
        return store
    if task.group_id:
        gs = group_stores.get(task.group_id)
        if gs is None:
            gs = InMemoryStore()
            group_stores[task.group_id] = gs
        return gs
    return InMemoryStore()


def _task_query_time(task: Task) -> Optional[float]:
    """``as_of`` query time: explicit metadata, else latest session ts, else None."""
    qt = task.metadata.get("query_time")
    if isinstance(qt, (int, float)):
        return float(qt)
    ts = [s.timestamp for s in task.sessions if s.timestamp]
    return max(ts) if ts else None


def _as_fraction(dev_slice: float | int, n: int) -> float:
    """Normalize a dev_slice (fraction in (0,1] or absolute count) to a fraction."""
    if isinstance(dev_slice, int) and not isinstance(dev_slice, bool):
        if dev_slice <= 0 or n == 0:
            return 0.0
        return min(1.0, dev_slice / n)
    frac = float(dev_slice)
    if frac <= 0:
        return 0.0
    if frac > 1.0:
        return min(1.0, frac / n) if n else 0.0
    return frac


__all__ = [
    "AgentAdapter",
    "AgentContext",
    "AgentResult",
    "run_agent",
    "EchoAgent",
    "function_agent",
]
