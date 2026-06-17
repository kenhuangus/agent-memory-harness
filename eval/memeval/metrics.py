"""The four evaluation metrics for the memory harness.

Pure, standard-library only. No third-party imports at module scope; a single
cosine over ``list[float]`` is implemented here with :mod:`math` (numpy is
*optional* and, if ever needed, must be imported lazily inside a function --
the offline path never touches it). Every public function is deterministic
given its inputs: it reads explicit timestamps off :class:`TrajectoryStep`
objects and never consults the wall clock.

The four metrics (see :class:`memeval.schema.Metrics` for ranges):

recency      Of queries whose gold-relevant memory is the *freshest* among the
             retrieved items, the fraction where that freshest relevant item is
             ranked #1 (rank == 0). Higher is better. Reported alongside a
             decayed variant ``mean(exp(-dt / tau))``.
efficiency   ``memory_tokens / total_tokens`` overhead per retrieval, averaged
             over tasks. LOWER is better (target < ~0.10).
relevancy    Mean similarity of retrieved items vs. the query, plus
             precision@k = fraction of retrieved items scoring >= threshold.
             Higher is better.
accuracy     Task success rate (QA normalized match / CODE tests pass), tracked
             memory-on vs. memory-off. Higher is better.

How tasks line up with trajectories
-----------------------------------
Recency and relevancy need each task's ``gold_memory_ids``; the functions build
a ``task_id -> Task`` map and join on :attr:`Trajectory.task_id`. The metrics
layer is also where :attr:`RetrievedItem.is_gold` gets set (invariant #6): a
retrieved item is gold iff its id is in the task's ``gold_memory_ids``. This is
done as a side effect on the trajectories passed in, so callers see the
annotation afterwards.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from .schema import (
    Metrics,
    RetrievedItem,
    Task,
    Trajectory,
    TrajectoryStep,
)

__all__ = [
    "THRESHOLD_DEFAULT",
    "TAU_DEFAULT",
    "cosine",
    "recency",
    "efficiency",
    "relevancy",
    "accuracy",
    "normalize_answer",
    "qa_match",
    "compute_metrics",
]


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
#: precision@k score threshold -- a retrieved item "counts" at or above this.
THRESHOLD_DEFAULT: float = 0.7

#: recency-decay time constant in seconds (1 day). Larger tau == slower decay.
TAU_DEFAULT: float = 86400.0


# --------------------------------------------------------------------------- #
# Vector math (tiny, stdlib-only)
# --------------------------------------------------------------------------- #
def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors: ``dot(a, b) / (||a|| * ||b||)``.

    Returns ``0.0`` when either vector is empty, length-mismatched, or a
    zero-vector (the similarity is undefined there, and 0 is the neutral value
    for the mean/precision aggregations downstream). No numpy: the dot product
    and norms are computed with a single pass each over the shared prefix.

    Math::

        cos(a, b) = (sum_i a_i * b_i) / (sqrt(sum_i a_i^2) * sqrt(sum_i b_i^2))
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# --------------------------------------------------------------------------- #
# Internal joins / helpers
# --------------------------------------------------------------------------- #
def _task_index(tasks: list[Task]) -> dict[str, Task]:
    """Map ``task_id -> Task`` (last write wins on duplicate ids)."""
    return {t.task_id: t for t in tasks}


def _retrieve_steps(traj: Trajectory) -> list[TrajectoryStep]:
    """Return the trajectory's ``retrieve`` steps that actually retrieved."""
    return [s for s in traj.steps if s.kind == "retrieve" and s.retrieved]


def _annotate_gold(traj: Trajectory, gold_ids: set[str]) -> None:
    """Set ``RetrievedItem.is_gold`` on every retrieved item in ``traj``.

    Invariant #6: gold-ness is decided by the metrics layer, not at search
    time. Mutates the trajectory in place so downstream consumers see it.
    """
    for step in traj.steps:
        for ri in step.retrieved:
            ri.is_gold = ri.item_id in gold_ids


def _freshest_gold(step: TrajectoryStep) -> Optional[RetrievedItem]:
    """Return the gold retrieved item with the latest timestamp, or ``None``.

    "Freshest" == max ``item.timestamp`` among the gold items in this step.
    Ties break toward the better (lower) rank so a tie does not unfairly fail
    the rank-#1 test.
    """
    golds = [ri for ri in step.retrieved if ri.is_gold]
    if not golds:
        return None
    return max(golds, key=lambda ri: (ri.timestamp, -ri.rank))


# --------------------------------------------------------------------------- #
# Metric 1: recency
# --------------------------------------------------------------------------- #
def recency(
    trajectories: list[Trajectory],
    tasks: list[Task],
    *,
    tau: float = TAU_DEFAULT,
) -> tuple[float, float]:
    """Recency of retrieval: is the freshest relevant memory ranked first?

    Returns ``(recency, recency_decayed)``.

    ``recency``
        Over tasks whose freshest gold-relevant *retrieved* item exists, the
        fraction where that freshest relevant item is ranked #1 (``rank == 0``)::

            recency = |{tasks: freshest_gold.rank == 0}| / |{tasks: freshest_gold exists}|

        A task with no gold item among its retrievals contributes to neither
        numerator nor denominator. ``0.0`` when no task has a freshest gold.

    ``recency_decayed``
        Mean over the same eligible tasks of ``exp(-dt / tau)`` where
        ``dt = max(0, query_time - item_time)`` for the freshest gold item::

            recency_decayed = mean( exp(-dt / tau) )   over eligible tasks

        ``query_time`` is the timestamp of the retrieve step that produced the
        item (so older retrievals decay more); ``item_time`` is the item's own
        timestamp. ``dt`` is clamped to ``>= 0`` (no negative / future ages).
        With ``tau <= 0`` decay is treated as instantaneous: score is 1.0 only
        when ``dt == 0``, else 0.0.

    Side effect: sets :attr:`RetrievedItem.is_gold` on the passed trajectories.
    """
    by_id = _task_index(tasks)
    ranked_first = 0
    eligible = 0
    decay_sum = 0.0

    for traj in trajectories:
        task = by_id.get(traj.task_id)
        if task is None:
            continue
        gold_ids = set(task.gold_memory_ids)
        _annotate_gold(traj, gold_ids)
        if not gold_ids:
            continue

        # Pick the single freshest gold across all retrieve steps for the task,
        # remembering which step (==> query_time) it came from.
        best: Optional[RetrievedItem] = None
        best_query_time = 0.0
        for step in _retrieve_steps(traj):
            cand = _freshest_gold(step)
            if cand is None:
                continue
            if best is None or cand.timestamp > best.timestamp or (
                cand.timestamp == best.timestamp and cand.rank < best.rank
            ):
                best = cand
                best_query_time = step.timestamp

        if best is None:
            continue

        eligible += 1
        if best.rank == 0:
            ranked_first += 1

        dt = max(0.0, best_query_time - best.timestamp)
        if tau <= 0.0:
            decay_sum += 1.0 if dt == 0.0 else 0.0
        else:
            decay_sum += math.exp(-dt / tau)

    if eligible == 0:
        return 0.0, 0.0
    return ranked_first / eligible, decay_sum / eligible


# --------------------------------------------------------------------------- #
# Metric 2: efficiency
# --------------------------------------------------------------------------- #
def efficiency(trajectories: list[Trajectory]) -> float:
    """Memory-token overhead ratio, averaged over tasks. LOWER is better.

    For each trajectory::

        ratio = memory_tokens / total_tokens

    where ``memory_tokens`` is the sum of retrieved items' token costs and
    ``total_tokens`` is the sum of ``tokens_in + tokens_out`` across generate
    steps (both are properties on :class:`Trajectory`). The reported metric is
    the mean ratio over trajectories that did any generation::

        efficiency = mean_over_tasks( memory_tokens / total_tokens )

    A trajectory with ``total_tokens == 0`` is skipped (no work to amortize
    against -- counting it as 0 would dilute the average misleadingly).
    Returns ``0.0`` when no trajectory generated any tokens. Target < ~0.10.
    """
    ratios: list[float] = []
    for traj in trajectories:
        total = traj.total_tokens
        if total <= 0:
            continue
        ratios.append(traj.memory_tokens / total)
    if not ratios:
        return 0.0
    return sum(ratios) / len(ratios)


# --------------------------------------------------------------------------- #
# Metric 3: relevancy
# --------------------------------------------------------------------------- #
def relevancy(
    trajectories: list[Trajectory],
    tasks: list[Task],
    *,
    threshold: float = THRESHOLD_DEFAULT,
    query_embeddings: Optional[dict[str, list[float]]] = None,
) -> tuple[float, float]:
    """Similarity of retrieved memory to the query + precision@k.

    Returns ``(mean_similarity, precision_at_k)``.

    For each retrieved item a similarity score is taken as:

    * ``cosine(query_emb, item.embedding)`` when ``query_embeddings`` is
      supplied (keyed by ``task_id``) *and* the item carries an embedding; else
    * the retriever-provided :attr:`RetrievedItem.score` (cosine in [0,1] by
      convention).

    Then::

        mean_similarity = mean( score_i )                    over all retrieved items
        precision_at_k  = |{i: score_i >= threshold}| / N    over all retrieved items

    Both are micro-averaged over every retrieved item in every retrieve step of
    every trajectory (so a task that retrieves more items weighs more). Returns
    ``(0.0, 0.0)`` when nothing was retrieved.
    """
    scores: list[float] = []

    for traj in trajectories:
        q_emb: Optional[list[float]] = None
        if query_embeddings is not None:
            q_emb = query_embeddings.get(traj.task_id)
        for step in _retrieve_steps(traj):
            for ri in step.retrieved:
                if q_emb is not None and ri.item.embedding is not None:
                    scores.append(cosine(q_emb, ri.item.embedding))
                else:
                    scores.append(ri.score)

    if not scores:
        return 0.0, 0.0
    mean_sim = sum(scores) / len(scores)
    precision = sum(1 for s in scores if s >= threshold) / len(scores)
    return mean_sim, precision


# --------------------------------------------------------------------------- #
# Metric 4: accuracy
# --------------------------------------------------------------------------- #
def accuracy(trajectories: list[Trajectory]) -> float:
    """Task success rate over graded trajectories. Higher is better.

    Counts only trajectories whose :attr:`Trajectory.success` is a real bool
    (``None`` means "not graded yet" and is ignored)::

        accuracy = |{traj: success is True}| / |{traj: success is not None}|

    Returns ``0.0`` when no trajectory has been graded.
    """
    graded = [t for t in trajectories if t.success is not None]
    if not graded:
        return 0.0
    return sum(1 for t in graded if t.success is True) / len(graded)


# --------------------------------------------------------------------------- #
# QA grading helpers
# --------------------------------------------------------------------------- #
_ARTICLES = {"a", "an", "the"}
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_answer(text: str) -> str:
    """Normalize a QA string for exact match (SQuAD-style).

    Lowercases, removes punctuation, drops the articles ``a/an/the``, and
    collapses whitespace. Empty / ``None``-ish input normalizes to ``""``::

        "The  Eiffel-Tower!" -> "eiffel tower"
    """
    if not text:
        return ""
    lowered = text.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    tokens = [w for w in no_punct.split() if w not in _ARTICLES]
    return _WS_RE.sub(" ", " ".join(tokens)).strip()


def qa_match(prediction: str, gold: str) -> bool:
    """Normalized exact match with substring tolerance.

    ``True`` when the normalized gold and prediction are equal, or when the
    (non-empty) normalized gold appears as a substring of the normalized
    prediction (the model often answers in a fuller sentence)::

        match = norm(gold) == norm(pred)  OR  norm(gold) in norm(pred)

    An empty normalized gold only matches an empty normalized prediction.
    """
    np_ = normalize_answer(prediction)
    ng = normalize_answer(gold)
    if not ng:
        return np_ == ""
    if np_ == ng:
        return True
    return ng in np_


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
def compute_metrics(
    trajectories: list[Trajectory],
    tasks: list[Task],
    *,
    tau: float = TAU_DEFAULT,
    threshold: float = THRESHOLD_DEFAULT,
    accuracy_memory_off: Optional[float] = None,
    query_embeddings: Optional[dict[str, list[float]]] = None,
) -> Metrics:
    """Aggregate all four metrics into a :class:`~memeval.schema.Metrics`.

    Runs :func:`recency`, :func:`efficiency`, :func:`relevancy`, and
    :func:`accuracy` over the (trajectories, tasks) join and packs the results::

        Metrics(
            recency, recency_decayed,            # from recency(...)
            efficiency,                          # from efficiency(...)
            relevancy, precision_at_k,           # from relevancy(...)
            accuracy,                            # from accuracy(...)
            accuracy_memory_off,                 # passed through for lift
            n = len(trajectories),
        )

    ``accuracy_memory_off`` is stored as-is so :attr:`Metrics.accuracy_lift`
    can report (memory-on - memory-off). As a side effect, this sets
    :attr:`RetrievedItem.is_gold` on the trajectories (via :func:`recency`).
    Deterministic: identical inputs always yield identical Metrics.
    """
    rec, rec_decayed = recency(trajectories, tasks, tau=tau)
    eff = efficiency(trajectories)
    rel, prec_at_k = relevancy(
        trajectories, tasks, threshold=threshold, query_embeddings=query_embeddings
    )
    acc = accuracy(trajectories)

    return Metrics(
        recency=rec,
        efficiency=eff,
        relevancy=rel,
        accuracy=acc,
        recency_decayed=rec_decayed,
        precision_at_k=prec_at_k,
        accuracy_memory_off=accuracy_memory_off,
        n=len(trajectories),
    )
