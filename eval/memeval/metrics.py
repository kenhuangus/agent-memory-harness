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
relevancy    precision@k = fraction of retrieved items that are GOLD
             (``is_gold``, from the benchmark's ``gold_memory_ids``), micro-
             averaged over retrieve steps. Scorer-AGNOSTIC: it measures what was
             retrieved, not the team's retriever-score distribution, so it is
             comparable across Jaccard / BM25 / embeddings. ``mean_similarity``
             keeps the embedding-cosine meaning when query embeddings are
             supplied, else mirrors the gold precision. Higher is better.
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
    # VISTA-derived reusable scoring additions (backward-compatible, additive).
    "retrieval_calibration",
    "reliability_bins",
    "ece",
    "mce",
    "brier",
    "pass_hat_k",
]


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
#: Legacy precision@k score threshold. The default (offline) relevancy path now
#: uses scorer-agnostic GOLD precision and ignores this; it is retained only for
#: the embedding-cosine mean_similarity interpretation and call-site compat.
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

    **precision_at_k is scorer-AGNOSTIC gold precision.** It is the fraction of
    retrieved items that are gold (``RetrievedItem.is_gold``), micro-averaged
    over every retrieved item in every retrieve step::

        precision_at_k = |{retrieved items that are gold}| / |{retrieved items}|

    Gold-ness comes from the benchmark's ground truth (``Task.gold_memory_ids``,
    annotated onto the items by :func:`_annotate_gold`), NOT from the retriever's
    own :attr:`RetrievedItem.score`. This is deliberate: a score-threshold
    precision measured the *shape* of the team's scorer distribution rather than
    retrieval relevance, so the SAME retrieval quality read ~0.005 under the old
    length-coupled Jaccard scorer and ~0.57 under BM25. Tying precision to gold
    membership makes the metric reflect *what was actually retrieved* and stay
    comparable across any present or future scorer (BM25, embeddings, anything),
    without touching the store scorer. ``threshold`` is no longer used by the
    gold-precision path; it is retained only for the embedding-cosine
    ``mean_similarity`` interpretation below and for call-site compatibility.

    ``mean_similarity`` keeps the embedding-cosine meaning when ``query_embeddings``
    is supplied (the only genuine ``[0, 1]`` semantic measure): the mean of
    ``cosine(query_emb, item.embedding)`` over items that carry an embedding. When
    no query embeddings are available (the offline/default path) there is no
    query-vs-item similarity to compute, so ``mean_similarity`` falls back to the
    same scorer-agnostic gold precision as ``precision_at_k`` (rather than
    reporting a scorer-shape artifact).

    Both are micro-averaged over every retrieved item in every retrieve step of
    every trajectory (so a task that retrieves more items weighs more). Returns
    ``(0.0, 0.0)`` when nothing was retrieved. As a side effect, sets
    :attr:`RetrievedItem.is_gold` on the passed trajectories (idempotent with
    :func:`recency`'s annotation).
    """
    by_id = _task_index(tasks)
    gold_flags: list[bool] = []      # is_gold over every retrieved item
    cosine_scores: list[float] = []  # only items with an embedding + a query emb

    for traj in trajectories:
        task = by_id.get(traj.task_id)
        if task is not None:
            _annotate_gold(traj, set(task.gold_memory_ids))
        q_emb: Optional[list[float]] = None
        if query_embeddings is not None:
            q_emb = query_embeddings.get(traj.task_id)
        for step in _retrieve_steps(traj):
            for ri in step.retrieved:
                gold_flags.append(bool(ri.is_gold))
                if q_emb is not None and ri.item.embedding is not None:
                    cosine_scores.append(cosine(q_emb, ri.item.embedding))

    if not gold_flags:
        return 0.0, 0.0
    precision = sum(1 for g in gold_flags if g) / len(gold_flags)
    if cosine_scores:
        mean_sim = sum(cosine_scores) / len(cosine_scores)
    else:
        # No query embeddings -> no scorer-independent similarity to report;
        # mirror the gold precision rather than expose a scorer-shape artifact.
        mean_sim = precision
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
    """Normalized exact match with whole-word containment tolerance.

    ``True`` when the normalized gold and prediction are equal, or when the
    (non-empty) normalized gold tokens appear as a contiguous run of WHOLE
    words inside the normalized prediction (the model often answers in a fuller
    sentence)::

        match = tokens(gold) == tokens(pred)
                OR tokens(gold) is a contiguous sub-sequence of tokens(pred)

    The containment test is at the *token* (word) boundary, NOT a raw character
    substring: gold ``"7"`` no longer matches ``"17 shirts"`` (``7`` is a
    substring of ``17`` but not a whole word), and gold ``"each way"`` only
    matches a prediction whose words are ``... each way ...`` in that order.
    This eliminates the digit-inside-a-number class of false positives while
    keeping the fuller-sentence tolerance the harness relies on.

    An empty normalized gold only matches an empty normalized prediction.

    Known (deterministic-grader) limitations, documented rather than papered
    over with a non-deterministic LLM judge (see ``suggestion.md``):

    * **Negation/list false positives** — a prediction that explicitly denies
      the gold ("your name was *not* Johnson") still contains the gold token as
      a whole word and so is credited. Whole-word matching does not detect
      negation; only an LLM judge would, at the cost of reproducibility.
    * **Paraphrase/synonym/abbreviation false negatives** — "14th of February"
      vs gold "February 14th", "ten" vs "10", "Business Admin" vs "Business
      Administration" do not match. These are inherent to deterministic exact
      matching and cap, but never inflate, achievable accuracy.
    """
    np_tokens = normalize_answer(prediction).split()
    ng_tokens = normalize_answer(gold).split()
    if not ng_tokens:
        return not np_tokens
    if np_tokens == ng_tokens:
        return True
    # Whole-word contiguous containment: does the gold token list appear as a
    # contiguous run somewhere in the prediction token list?
    n = len(ng_tokens)
    for start in range(len(np_tokens) - n + 1):
        if np_tokens[start:start + n] == ng_tokens:
            return True
    return False


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# VISTA-derived reusable scoring additions (additive; existing metrics intact)
# --------------------------------------------------------------------------- #
# These port three VISTA scoring patterns into the harness's shared metrics
# layer, reframed for memory retrieval:
#
# * retrieval_calibration — precision / recall / F1 of *gold* retrieval, the
#   recall/F1 view our scorer-agnostic precision@k (relevancy) does not give.
#   Mirrors VISTA's verification_calibration (precision/recall/F1 over the
#   high-risk escalation forks): "did the agent retrieve the relevant memory
#   when it existed, and not surface irrelevant memory when it didn't?".
# * ECE / Brier calibration — ported verbatim-in-spirit from
#   ``vista-benchmark/analysis/calibration.py`` (Guo et al. 1706.04599;
#   Brier 1950) for any confidence-bearing answer the harness produces.
# * pass^k reliability — ported from ``vista-benchmark/harness/scorer.py``;
#   meaningful ONLY for the STOCHASTIC Claude Code path (the deterministic
#   native EchoAgent path collapses to pass@1), so callers must guard it.
#
# All pure / deterministic: no wall-clock, no RNG, no LLM, no network.
def retrieval_calibration(
    trajectories: list[Trajectory],
    tasks: list[Task],
) -> dict[str, float]:
    """Precision / recall / F1 of GOLD memory retrieval, micro-averaged.

    Reframes VISTA's ``verification_calibration`` for memory retrieval. Over
    every retrieve step of every trajectory:

    * **true positive** — a retrieved item that is gold (in the task's
      ``gold_memory_ids``).
    * **false positive** — a retrieved item that is NOT gold (noise surfaced).
    * **false negative** — a gold id that was never retrieved across the task's
      retrieve steps (relevant memory the agent failed to surface).

    Precision = TP/(TP+FP); recall = TP/(TP+FN); F1 = harmonic mean. Each
    defaults to ``1.0`` when its denominator is 0 (no opportunity to be wrong =
    vacuously calibrated), matching VISTA's convention. Existing ``relevancy``
    (precision@k only) is untouched; this is the additive recall/F1 view.

    Side effect: sets :attr:`RetrievedItem.is_gold` (idempotent with relevancy).
    """
    by_id = _task_index(tasks)
    tp = fp = fn = 0
    for traj in trajectories:
        task = by_id.get(traj.task_id)
        if task is None:
            continue
        gold_ids = set(task.gold_memory_ids)
        _annotate_gold(traj, gold_ids)
        retrieved_ids: set[str] = set()
        for step in _retrieve_steps(traj):
            for ri in step.retrieved:
                retrieved_ids.add(ri.item_id)
                if ri.is_gold:
                    tp += 1
                else:
                    fp += 1
        # gold ids never retrieved anywhere in this task -> false negatives.
        fn += len(gold_ids - retrieved_ids)
    precision = 1.0 if (tp + fp) == 0 else tp / (tp + fp)
    recall = 1.0 if (tp + fn) == 0 else tp / (tp + fn)
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positive": float(tp),
        "false_positive": float(fp),
        "false_negative": float(fn),
    }


def reliability_bins(
    confidences: list[float], outcomes: list[float], n_bins: int = 10
) -> list[dict]:
    """Equal-width [0,1] bins; each reports count, mean confidence, accuracy.

    Ported from ``vista-benchmark/analysis/calibration.py``. A confidence of
    exactly 1.0 lands in the top bin. ``outcomes`` are 0/1 (or in [0,1]).
    """
    bins: list[dict] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        idx = [j for j, c in enumerate(confidences)
               if (lo <= c < hi) or (i == n_bins - 1 and c == 1.0)]
        if idx:
            conf_mean = sum(confidences[j] for j in idx) / len(idx)
            acc = sum(outcomes[j] for j in idx) / len(idx)
        else:
            conf_mean = acc = 0.0
        bins.append({"lo": lo, "hi": hi, "count": len(idx),
                     "conf_mean": conf_mean, "acc": acc})
    return bins


def ece(confidences: list[float], outcomes: list[float], n_bins: int = 10) -> float:
    """Expected calibration error: ``sum_b (|b|/N) * |acc(b) - conf(b)|``.

    Ported from VISTA ``analysis/calibration.py``. ``0.0`` for empty input.
    """
    n = len(confidences)
    if n == 0:
        return 0.0
    return sum((b["count"] / n) * abs(b["acc"] - b["conf_mean"])
               for b in reliability_bins(confidences, outcomes, n_bins) if b["count"])


def mce(confidences: list[float], outcomes: list[float], n_bins: int = 10) -> float:
    """Maximum calibration error: the worst single-bin gap. (VISTA port.)"""
    gaps = [abs(b["acc"] - b["conf_mean"])
            for b in reliability_bins(confidences, outcomes, n_bins) if b["count"]]
    return max(gaps) if gaps else 0.0


def brier(confidences: list[float], outcomes: list[float]) -> float:
    """Brier score: mean squared error of the stated probabilities. (VISTA port.)"""
    n = len(confidences)
    if n == 0:
        return 0.0
    return sum((c - o) ** 2 for c, o in zip(confidences, outcomes)) / n


def pass_hat_k(passes: list[bool], k: int) -> float:
    """pass^k = fraction of size-``k`` consecutive windows where ALL runs pass.

    Ported from ``vista-benchmark/harness/scorer.py::pass_hat_k`` — the
    deterministic, RNG-free pass^k estimator (tau-bench style). With ``k == n``
    it collapses to "all runs passed". Raises on ``k < 1`` or ``k > n``.

    GUARD: this is only meaningful for the STOCHASTIC Claude Code agent path,
    where repeated runs of the same task can differ. The native EchoAgent path
    is deterministic (byte-identical runs), so pass^k there is trivially pass@1
    and should NOT be reported — callers must apply pass^k only over multi-seed
    stochastic results.
    """
    n = len(passes)
    if k < 1:
        raise ValueError("k must be >= 1")
    if k > n:
        raise ValueError(f"k ({k}) cannot exceed number of runs ({n})")
    windows = n - k + 1
    good = sum(1 for i in range(windows) if all(passes[i:i + k]))
    return good / windows


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
