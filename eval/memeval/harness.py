"""The evaluation harness -- ties loaders + models + memory + metrics + cost.

:func:`run` is the one entry point that drives any of the five benchmarks
through any :class:`~memeval.protocols.ModelAdapter`, with memory on or off,
and returns a fully-populated :class:`~memeval.schema.RunResult`.

Flow per task
-------------
1. **Ingest** the task's sessions into the :class:`MemoryStore` (memory-on).
2. **Retrieve** the top-``k`` memories for the question, honoring ``as_of`` so
   nothing newer than the query is visible. Record a ``retrieve`` step whose
   :class:`RetrievedItem` list carries each item's tokens + 0-based rank --
   this is what the efficiency / recency / relevancy metrics read.
3. **Generate** a prediction. The retrieved memory is injected into the prompt
   as ``[memory] ...`` lines, which is *exactly* what makes the offline
   :class:`EchoModel` memory-sensitive (it echoes a retrieved line when one is
   present, and otherwise falls back to the question and typically misses).
   Token counts come back from the adapter and are charged to the
   :class:`CostTracker`.
4. **Grade** QA tasks with normalized exact match; CODE tasks are graded
   externally (success left ``None`` unless a grader is supplied), so accuracy
   over a CODE run reflects only graded trajectories.
5. **Log** the trajectory (optional JSONL) and, after the loop, compute the
   four metrics over all trajectories.

Determinism
-----------
No wall-clock enters the *logic*: retrieval ``as_of`` and recency use the
explicit task/session timestamps. ``started_at``/``ended_at`` are wall-clock
*metadata* only (sourced from an injectable ``clock`` for test stability) and
never feed a metric. Given the same inputs the same metrics come out.

Cheapest-first
--------------
:func:`cheapest_first` orders configs Haiku+mem -> Haiku -> Sonnet -> Opus so a
sweep spends the least first; :func:`should_early_exit` lets a sweep stop once a
cheap config clears the target accuracy. :func:`stratified_dev_slice` draws a
deterministic per-competency sample for fast dev iteration.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any, Callable, Optional

from .cost import BudgetExceeded, CostTracker
from .loaders import get_loader
from .metrics import compute_metrics, qa_match
from .models import estimate_tokens
from .protocols import MemoryStore, ModelAdapter
from . import tracing
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


# --------------------------------------------------------------------------- #
# InMemoryStore -- reference MemoryStore (stdlib only)
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens (alnum runs). Stdlib-only, deterministic."""
    out: list[str] = []
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


# Okapi BM25 constants (standard defaults). Baked in deliberately: callers must
# NOT change the frozen ``search`` signature to tune them -- pass via **kwargs if
# ever needed (out of scope here).
BM25_K1: float = 1.5
BM25_B: float = 0.75


def _bm25_scores(
    query: str,
    docs: list[tuple[str, str]],
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> dict[str, tuple[float, float]]:
    """Okapi BM25 over ``docs`` (``[(item_id, content), ...]``), stdlib-only.

    Returns ``{item_id: (bm25_score, idf_coverage)}`` for every doc:

    * ``bm25_score`` -- the non-negative, *unbounded* Okapi BM25 relevance of the
      doc to ``query`` over the supplied corpus. Length enters only through the
      bounded ``b * |d| / avgdl`` saturation term, so a long gold turn that
      contains the query terms is no longer crushed the way Jaccard's ``|q ∪ d|``
      denominator crushed it.
    * ``idf_coverage`` -- ``sum(idf(t) for t in q_terms ∩ d)``: the SECONDARY
      tie-break value, favoring a doc that matches the rarer (higher-IDF) query
      terms. Sort-only; never stored on a :class:`RetrievedItem`.

    Determinism: ``df``/``idf``/``tf`` are pure functions of ``(corpus, query)``;
    query terms are iterated in ``sorted`` order for both the IDF build and the
    per-doc summation so floating-point accumulation order is fixed cross-platform.

    IDF form: ``log((N - df + 0.5) / (df + 0.5) + 1.0)`` -- the ``+1.0`` inside
    the log floors IDF at ``>= 0``, removing BM25's classic negative weight for
    terms appearing in more than half the corpus. An empty query (no terms)
    yields ``0.0`` for every doc. ``avgdl <= 0`` degrades the length-norm factor
    to ``(1 - b)`` (no division by zero).
    """
    q_terms = sorted(set(_tokenize(query)))
    if not q_terms or not docs:
        return {doc_id: (0.0, 0.0) for doc_id, _ in docs}

    tokenized: list[tuple[str, Counter[str], int]] = []
    total_len = 0
    for doc_id, content in docs:
        toks = _tokenize(content)
        tf = Counter(toks)
        tokenized.append((doc_id, tf, len(toks)))
        total_len += len(toks)

    n = len(tokenized)
    avgdl = (total_len / n) if n else 0.0

    # df + idf over the sorted query terms (stable accumulation order).
    idf: dict[str, float] = {}
    for t in q_terms:
        df = sum(1 for _id, tf, _dl in tokenized if t in tf)
        idf[t] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    scores: dict[str, tuple[float, float]] = {}
    for doc_id, tf, dl in tokenized:
        if avgdl > 0.0:
            norm = 1.0 - b + b * (dl / avgdl)
        else:
            norm = 1.0 - b
        bm25 = 0.0
        cover = 0.0
        for t in q_terms:  # sorted -> deterministic float accumulation
            f = tf.get(t, 0)
            if f <= 0:
                continue
            it = idf[t]
            bm25 += it * (f * (k1 + 1.0)) / (f + k1 * norm)
            cover += it
        scores[doc_id] = (bm25, cover)
    return scores


class InMemoryStore:
    """Reference :class:`~memeval.protocols.MemoryStore` (standard library).

    Retrieval is Okapi BM25 (``k1=1.5``, ``b=0.75``) over the ``as_of``-filtered
    corpus (see :func:`_bm25_scores`). Scores are non-negative and **NOT** bounded
    to ``[0, 1]`` -- length couples in only through BM25's bounded saturation term,
    so a long gold turn containing the query terms is no longer crushed the way
    Jaccard's ``|q ∪ d|`` denominator crushed it, and IDF up-weights the rare,
    discriminative terms that uniquely identify the gold item. Ties break by higher
    IDF-weighted query coverage, then write-time ``relevancy``, then more-recent
    ``timestamp``, then ``item_id`` (so results are fully deterministic). ``write``
    is idempotent on ``item_id``. ``search`` honors ``as_of`` (no items newer than
    the query), sets each :class:`RetrievedItem`'s 0-based ``rank`` and ensures the
    underlying ``MemoryItem.tokens`` is populated (estimated if zero) so the
    efficiency metric has a number to divide.
    """

    def __init__(self) -> None:
        self._items: dict[str, MemoryItem] = {}
        self._order: list[str] = []  # insertion order, for stable iteration

    def write(self, item: MemoryItem) -> None:
        """Persist ``item`` (overwrites any existing item with the same id)."""
        if item.tokens <= 0 and item.content:
            item.tokens = estimate_tokens(item.content)
        if item.item_id not in self._items:
            self._order.append(item.item_id)
        self._items[item.item_id] = item

    def get(self, item_id: str) -> Optional[MemoryItem]:
        """Return the item with ``item_id`` or ``None``."""
        return self._items.get(item_id)

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        as_of: Optional[float] = None,
        **kwargs: Any,
    ) -> list[RetrievedItem]:
        """Top-``k`` items by Okapi BM25, best first, with rank+tokens set."""
        # Gather the as_of-filtered candidate corpus (no peeking at the future),
        # then score every candidate with a single BM25 pass over that corpus.
        candidates: list[MemoryItem] = []
        for item_id in self._order:
            item = self._items[item_id]
            if as_of is not None and item.timestamp > as_of:
                continue
            candidates.append(item)

        bm25 = _bm25_scores(query, [(it.item_id, it.content) for it in candidates])

        # Deterministic ordering: BM25 desc, IDF-coverage desc, relevancy desc,
        # timestamp desc, item_id asc. ``item_id`` (unique) is the final key so
        # the sort fully orders every tie -- empty query => all scores 0.0, which
        # leaves the established relevancy/timestamp/id tie-breaks in control.
        def sort_key(it: MemoryItem) -> tuple[float, float, float, float, str]:
            score, cover = bm25[it.item_id]
            return (-score, -cover, -it.relevancy, -it.timestamp, it.item_id)

        candidates.sort(key=sort_key)

        results: list[RetrievedItem] = []
        for rank, item in enumerate(candidates[: max(0, k)]):
            if item.tokens <= 0 and item.content:
                item.tokens = estimate_tokens(item.content)
            score = bm25[item.item_id][0]
            results.append(RetrievedItem(item=item, score=score, rank=rank))
        return results

    def all(self) -> list[MemoryItem]:
        """Every stored item, in insertion order (used by the dreaming worker)."""
        return [self._items[i] for i in self._order]

    def delete(self, item_id: str) -> bool:
        """Remove ``item_id``; return ``True`` if it was present (idempotent)."""
        if item_id not in self._items:
            return False
        del self._items[item_id]
        self._order.remove(item_id)
        return True


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #
def _build_prompt(task: Task, retrieved: list[RetrievedItem]) -> str:
    """Assemble the model prompt, injecting retrieved memory as ``[memory]`` lines.

    The ``[memory] <content>`` lines are the contract with :class:`EchoModel`:
    when memory carrying the answer is retrieved it echoes it (scores correct);
    with no memory it falls back to the question and typically misses -- which
    is precisely the memory-on vs memory-off lift the dashboard reports.

    Items are emitted **worst-ranked first, best-ranked last** so the most
    relevant memory sits closest to the question (the position a model weights
    most, and the ``[memory]`` line :class:`EchoModel` anchors on).
    """
    parts: list[str] = []
    # ``retrieved`` is best-first (rank 0 == top); reverse so the top hit is last.
    for r in sorted(retrieved, key=lambda x: x.rank, reverse=True):
        content = r.item.content.replace("\n", " ").strip()
        if content:
            parts.append(f"[memory] {content}")
    parts.append(f"Question: {task.question}")
    if task.choices:
        parts.append("Choices: " + " | ".join(task.choices))
    parts.append("Answer:")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #
def run(
    benchmark: "Benchmark | str",
    model: ModelAdapter,
    memory: bool,
    *,
    limit: Optional[int] = None,
    store: Optional[MemoryStore] = None,
    logger: Optional[TrajectoryLogger] = None,
    cost: Optional[CostTracker] = None,
    dev_slice: Optional[float | int] = None,
    path_or_id: Optional[str] = None,
    config: Optional[ModelConfig] = None,
    tau: float = 86400.0,
    threshold: float = 0.7,
    k: int = 5,
    grader: Optional[Callable[[Task, str], Optional[bool]]] = None,
    clock: Callable[[], float] = time.time,
) -> RunResult:
    """Run one benchmark through one model+memory configuration.

    Parameters
    ----------
    benchmark:
        Benchmark enum or loose string (resolved via the loader registry).
    model:
        Any :class:`~memeval.protocols.ModelAdapter` (``EchoModel`` offline,
        ``AnthropicAdapter`` online).
    memory:
        When True, ingest each task's sessions into ``store`` and retrieve the
        top-``k`` before generating; when False, no store is consulted (the
        memory-off baseline).
    limit:
        Cap the number of tasks (after any ``dev_slice`` sampling).
    store:
        Memory backend; defaults to a fresh :class:`InMemoryStore`. Reset
        per task so retrieval can't leak across unrelated tasks (except within
        a ``group_id`` -- continual-learning groups keep their store).
    logger:
        Optional :class:`TrajectoryLogger`; each trajectory is logged as JSONL.
    cost:
        Optional :class:`CostTracker`; a :class:`BudgetExceeded` aborts the run
        and returns a *partial* :class:`RunResult` with what completed.
    dev_slice:
        Fraction (0,1] or absolute int -> stratified dev sample of the tasks.
    path_or_id:
        Local fixture path or remote dataset id passed to the loader.
    config:
        Optional :class:`ModelConfig` describing this cell; one is synthesized
        from the model + ``memory`` flag if omitted.
    tau, threshold, k:
        Recency decay constant, relevancy precision threshold, retrieval depth.
    grader:
        Optional ``(task, prediction) -> Optional[bool]`` override (e.g. an
        external CODE grader). Defaults to normalized QA exact match for QA
        tasks and ``None`` (ungraded) for CODE tasks.

    Returns
    -------
    RunResult
        Aggregate metrics + per-task trajectories + cost. ``partial`` is True
        if the run was truncated by ``limit``/``dev_slice``/budget abort.
    """
    bench = benchmark if isinstance(benchmark, Benchmark) else Benchmark.from_str(benchmark)
    cfg = config or _config_for(model, memory)

    started_at = clock()

    loader = get_loader(bench)
    all_tasks = loader.load(path_or_id, limit=None)
    total_available = len(all_tasks)

    tasks = all_tasks
    truncated = False
    if dev_slice is not None:
        tasks = stratified_dev_slice(tasks, _as_fraction(dev_slice, len(tasks)))
        truncated = truncated or len(tasks) < total_available
    if limit is not None:
        if len(tasks) > limit:
            truncated = True
        tasks = tasks[:limit]

    trajectories: list[Trajectory] = []
    budget_hit = False

    # Group-scoped stores for continual-learning benchmarks: tasks sharing a
    # group_id share memory (carried in chronological `order`); ungrouped tasks
    # each get a clean store so retrieval can't leak across unrelated tasks.
    group_stores: dict[str, MemoryStore] = {}

    for task in tasks:
        active_store = _store_for_task(task, store, group_stores)
        try:
            traj = _run_task(
                task=task,
                model=model,
                memory=memory,
                store=active_store,
                cost=cost,
                k=k,
                grader=grader,
                clock=clock,
                config=cfg,
            )
        except BudgetExceeded:
            budget_hit = True
            break
        trajectories.append(traj)
        if logger is not None:
            logger.log(traj)

    ended_at = clock()

    # Metrics over everything that completed (deterministic; no wall-clock).
    metrics: Metrics = compute_metrics(
        trajectories, tasks[: len(trajectories)], tau=tau, threshold=threshold
    )

    tokens_in = sum(s.tokens_in for t in trajectories for s in t.steps)
    tokens_out = sum(s.tokens_out for t in trajectories for s in t.steps)
    cost_usd = cost.spent_usd if cost is not None else 0.0
    partial = truncated or budget_hit or len(trajectories) < total_available

    # Mirror the aggregate metrics into Langfuse as run-level scores (no-op when
    # tracing is disabled). Matches the run_agent path so single-shot and
    # multi-step runs trace identically.
    with tracing.task_span(
        f"run:{bench.value}:{cfg.label}",
        metadata={"cost_usd": cost_usd, "n_tasks": len(trajectories), "partial": partial},
    ) as rspan:
        for _m in ("recency", "efficiency", "relevancy", "accuracy"):
            rspan.score(_m, getattr(metrics, _m))
    tracing.flush()

    return RunResult(
        benchmark=bench,
        config=cfg,
        metrics=metrics,
        trajectories=trajectories,
        n_tasks=len(trajectories),
        cost_usd=cost_usd,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        budget_exceeded=budget_hit,
        partial=partial,
        started_at=started_at,
        ended_at=ended_at,
        metadata={
            "memory": memory,
            "k": k,
            "tau": tau,
            "threshold": threshold,
            "total_available": total_available,
            "limit": limit,
            "source": path_or_id or getattr(loader, "default_source", ""),
        },
    )


def _run_task(
    *,
    task: Task,
    model: ModelAdapter,
    memory: bool,
    store: MemoryStore,
    cost: Optional[CostTracker],
    k: int,
    grader: Optional[Callable[[Task, str], Optional[bool]]],
    clock: Callable[[], float],
    config: ModelConfig,
) -> Trajectory:
    """Execute a single task and return its :class:`Trajectory`.

    Raises :class:`BudgetExceeded` (propagated to :func:`run`) if charging the
    generate call would breach the budget -- nothing is committed in that case.
    """
    traj = Trajectory(
        task_id=task.task_id,
        benchmark=task.benchmark,
        model=model.name,
        memory_on=memory,
        started_at=clock(),
    )

    # Query time drives `as_of` (None => no temporal filter, no future-peeking).
    # The trajectory step timestamp must be a concrete float per the schema, so
    # an absent query time records as 0.0 while `as_of` stays None.
    query_time = _task_query_time(task)
    step_ts = query_time if query_time is not None else 0.0

    with tracing.task_span(
        task.task_id,
        input=task.question,
        metadata={"benchmark": task.benchmark.value, "memory": memory, "model": model.name},
    ) as tspan:
        retrieved: list[RetrievedItem] = []
        if memory:
            # Ingest this task's sessions, then retrieve.
            for sess in task.sessions:
                store.write(MemoryItem.from_session(sess))
            retrieved = store.search(task.question, k=k, as_of=query_time)
            traj.add(
                TrajectoryStep(
                    step=0,
                    kind="retrieve",
                    content=task.question,
                    timestamp=step_ts,
                    retrieved=retrieved,
                )
            )
            tspan.step(
                "retrieve", "retrieve", input=task.question,
                metadata={"k": len(retrieved), "hits": [
                    {"id": h.item_id, "score": round(h.score, 4), "rank": h.rank}
                    for h in retrieved
                ]},
            )

        prompt = _build_prompt(task, retrieved)
        text, tin, tout = model.generate(
            prompt,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        # Charge cost BEFORE recording the generate step so a budget breach aborts
        # cleanly without a half-recorded trajectory committed to the run.
        if cost is not None:
            cost.add(model.name, tin, tout)  # may raise BudgetExceeded

        traj.add(
            TrajectoryStep(
                step=0,
                kind="generate",
                content=text,
                timestamp=step_ts,
                tokens_in=tin,
                tokens_out=tout,
            )
        )
        tspan.step("generate", "generate", output=text, tokens_in=tin, tokens_out=tout)

        traj.prediction = text
        traj.success = _grade(task, text, grader)
        traj.ended_at = clock()
        tspan.update(output=text)
    return traj


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #
def _grade(
    task: Task,
    prediction: str,
    grader: Optional[Callable[[Task, str], Optional[bool]]],
) -> Optional[bool]:
    """Grade a prediction. QA -> normalized exact match; CODE -> external.

    A custom ``grader`` (returning ``True``/``False``/``None``) wins when given.
    Without one: QA tasks with a gold ``answer`` are scored by
    :func:`memeval.metrics.qa_match`; CODE tasks (or QA without a gold) are
    left ``None`` (ungraded) so accuracy reflects only what was actually graded.
    """
    if grader is not None:
        return grader(task, prediction)
    if task.kind == TaskKind.QA and task.answer is not None:
        return qa_match(prediction, task.answer)
    return None


# --------------------------------------------------------------------------- #
# Store / timing helpers
# --------------------------------------------------------------------------- #
def _store_for_task(
    task: Task,
    store: Optional[MemoryStore],
    group_stores: dict[str, MemoryStore],
) -> MemoryStore:
    """Pick the memory store for ``task``.

    If the caller passed an explicit ``store``, always use it (the caller owns
    its lifecycle). Otherwise grouped tasks (``group_id``) share a per-group
    :class:`InMemoryStore` -- preserving continual-learning carry-over -- while
    ungrouped tasks each get a fresh store so unrelated tasks don't cross-talk.
    """
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
    """Determine the ``as_of`` query time for a task.

    Uses an explicit ``metadata['query_time']`` if present, else the latest
    session timestamp, else ``None`` (no temporal filter). Never wall-clock.
    """
    qt = task.metadata.get("query_time")
    if isinstance(qt, (int, float)):
        return float(qt)
    ts = [s.timestamp for s in task.sessions if s.timestamp]
    if ts:
        return max(ts)
    return None


# --------------------------------------------------------------------------- #
# Config + cheapest-first ordering + early-exit + dev slice
# --------------------------------------------------------------------------- #
DEFAULT_TIER_ORDER: list[str] = ["haiku", "sonnet", "opus"]


def _infer_tier(name: str) -> str:
    """Best-effort tier from a model name (for cheapest-first ordering)."""
    low = name.lower()
    for tier in DEFAULT_TIER_ORDER:
        if tier in low:
            return tier
    return ""  # unknown tiers sort after known ones


def _config_for(model: ModelAdapter, memory: bool) -> ModelConfig:
    """Synthesize a :class:`ModelConfig` from an adapter + memory flag."""
    return ModelConfig(
        name=model.name,
        memory=memory,
        price_in=getattr(model, "price_in", 0.0),
        price_out=getattr(model, "price_out", 0.0),
        tier=_infer_tier(model.name),
    )


def cheapest_first(configs: list[ModelConfig]) -> list[ModelConfig]:
    """Order configs cheapest-first: Haiku+mem -> Haiku -> Sonnet -> Opus.

    Sort key is ``(tier_index, memory_desc)`` so within a tier the memory-on
    variant comes first (the harness's whole bet is that cheap+memory wins, so
    it's evaluated before the pricier no-memory baselines). Unknown tiers sort
    last; ties fall back to name for determinism.
    """
    order = {t: i for i, t in enumerate(DEFAULT_TIER_ORDER)}

    def key(c: ModelConfig) -> tuple[int, int, str]:
        tier = c.tier or _infer_tier(c.name)
        tier_idx = order.get(tier, len(order))
        return (tier_idx, 0 if c.memory else 1, c.name)

    return sorted(configs, key=key)


def should_early_exit(results: list[RunResult], *, target_accuracy: float) -> bool:
    """True once any completed (non-partial-by-budget) run hits the target.

    Used by a cheapest-first sweep: stop spending on pricier configs as soon as
    a cheaper one clears ``target_accuracy``. A run aborted by budget does not
    count as a passing result.
    """
    for r in results:
        if r.budget_exceeded:
            continue
        if r.metrics.accuracy >= target_accuracy:
            return True
    return False


def _as_fraction(dev_slice: float | int, n: int) -> float:
    """Normalize a dev_slice (fraction in (0,1] or absolute count) to a fraction."""
    if isinstance(dev_slice, int) and not isinstance(dev_slice, bool):
        if dev_slice <= 0 or n == 0:
            return 0.0
        return min(1.0, dev_slice / n)
    frac = float(dev_slice)
    if frac <= 0:
        return 0.0
    if frac > 1.0:  # treat >1 as an absolute count
        return min(1.0, frac / n) if n else 0.0
    return frac


def stratified_dev_slice(
    tasks: list[Task], fraction: float = 0.12, *, seed: int = 0
) -> list[Task]:
    """Deterministic per-stratum sample of ``tasks``.

    Strata are :meth:`Task.stratum` (competency, else kind). Within each
    stratum the tasks are shuffled with a seeded RNG and the first
    ``ceil(fraction * stratum_size)`` are kept (at least 1 per non-empty
    stratum when ``fraction > 0``). Output preserves the input order of the
    selected tasks so downstream ordering (continual-learning) is stable.
    Deterministic given ``(tasks, fraction, seed)``.
    """
    import math
    import random

    if fraction <= 0 or not tasks:
        return []
    if fraction >= 1.0:
        return list(tasks)

    by_stratum: dict[str, list[int]] = {}
    for i, t in enumerate(tasks):
        by_stratum.setdefault(t.stratum(), []).append(i)

    keep: set[int] = set()
    for stratum, idxs in sorted(by_stratum.items()):
        rng = random.Random(f"{seed}:{stratum}")
        order = list(idxs)
        rng.shuffle(order)
        n_keep = max(1, math.ceil(fraction * len(order)))
        keep.update(order[:n_keep])

    return [tasks[i] for i in range(len(tasks)) if i in keep]


__all__ = [
    "InMemoryStore",
    "_bm25_scores",
    "run",
    "DEFAULT_TIER_ORDER",
    "cheapest_first",
    "should_early_exit",
    "stratified_dev_slice",
]
