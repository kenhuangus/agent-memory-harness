"""Native evaluator for **MemoryAgentBench** (arXiv:2507.05257).

MemoryAgentBench probes four memory *competencies* over an "inject once, query
many times" protocol: each dataset example is ONE very long context paired with
MANY questions. The native run, per context block, is:

1. split the context into sequential fixed-size chunks (the paper runs both 512-
   and 4096-token settings as separate configurations);
2. feed the chunks to the memory agent as an INCREMENTAL multi-turn stream so it
   writes/updates memory turn by turn;
3. AFTER the full stream is ingested, ask all of that context's questions and
   score each answer independently.

Memory is built FRESH per context block and is NOT carried across blocks (the
loader already expanded each ``(context, question[i], answer[i])`` into its own
:class:`~memeval.schema.Task`, so "one block, many questions" arrives here as a
set of sibling Tasks that share the same ``sessions``). Offline, the existing
:meth:`BaseNativeEvaluator.run_tasks` seam ingests each task's chunked sessions
into a per-task :class:`~memeval.harness.InMemoryStore`, retrieves, and lets the
deterministic :class:`~memeval.models.EchoModel` surface an answer from the
retrieved chunk — no network, no LLM.

Native metrics (paper-exact)
----------------------------
* **SubEM** (substring exact match) for **Accurate Retrieval** and **Conflict
  Resolution**: the gold answer is credited iff it appears as a *raw* normalized
  substring of the prediction. This is deliberately looser than the harness's
  :func:`memeval.metrics.qa_match` (whole-WORD containment), which would reject
  gold ``"7"`` inside ``"17"`` — the paper uses raw substring containment, so a
  local :func:`sub_em` implements that exactly. For CR the gold is the LATEST /
  final value among conflicting edits, so SubEM there is "latest-fact match".
* **EM** (exact match) for **Test-Time Learning** (ICL classification) and
  **Long-Range Understanding** (DetectiveQA): STRICT equality after light
  canonicalization (``"label: 43"`` ≠ ``"43"``). A local :func:`em` implements
  this; the substring-tolerant ``qa_match`` is intentionally NOT reused.
* **per-competency mean accuracy** — the four headline numbers, each the mean of
  its competency's per-question metric (SubEM for AR & CR, EM for TTL & LRU). The
  overall headline is the unweighted mean of the four competency scores.

With multiple acceptable golds (``Task.metadata['acceptable_answers']``) a
question is credited if ANY gold matches. The four core competencies need no LLM
judge (``judge_needed: false``); the ``judge`` arg is accepted for protocol
symmetry and ignored. The auxiliary recsys / longmemeval / infbench subsets
(Recall@5 / LLM-judge / F1-via-judge) are out of scope for the offline path.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...metrics import normalize_answer
from ...schema import Benchmark, Session, Task
from ..spec import (
    BenchmarkNativeReport,
    ComponentScore,
    NativeMetric,
    PerTaskRecord,
)
from .base import BaseNativeEvaluator, mean, mode_to_memory

# --------------------------------------------------------------------------- #
# Competency taxonomy
# --------------------------------------------------------------------------- #
#: The four canonical competencies the loader normalizes split names into.
_AR = "accurate_retrieval"
_TTL = "test_time_learning"
_LRU = "long_range_understanding"
_CR = "conflict_resolution"

#: Competencies scored with SubEM (substring) vs EM (strict).
_SUBEM_COMPETENCIES = frozenset({_AR, _CR})
_EM_COMPETENCIES = frozenset({_TTL, _LRU})

#: The four components, in paper order, for a stable report layout.
_COMPONENT_ORDER = (_AR, _TTL, _LRU, _CR)

#: Default chunk sizes the paper runs (kept for provenance / CLI parity). The
#: offline run uses a single configured size (default 512) to stay deterministic
#: and fast; both are recorded in metadata.
_PAPER_CHUNK_SIZES = (512, 4096)
_DEFAULT_CHUNK_TOKENS = 512

#: Whitespace-word -> token inflation, matching ``models.estimate_tokens_words``.
_WORDS_TO_TOKENS = 1.3

_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Native metric functions (paper-exact; NOT reusing qa_match)
# --------------------------------------------------------------------------- #
def sub_em(prediction: str, gold: str) -> bool:
    """Substring exact match: normalized ``gold`` is a *raw* substring of pred.

    The paper credits an Accurate-Retrieval / Conflict-Resolution answer iff the
    (SQuAD-normalized) gold appears as a contiguous CHARACTER substring of the
    normalized prediction. This is looser than
    :func:`memeval.metrics.qa_match` (whole-word containment): gold ``"7"`` DOES
    match prediction ``"17"`` under SubEM, which is the paper's behavior. An
    empty normalized gold matches only an empty normalized prediction.
    """
    g = normalize_answer(gold)
    p = normalize_answer(prediction)
    if not g:
        return not p
    return g in p


def em(prediction: str, gold: str) -> bool:
    """Strict exact match for Test-Time-Learning / Long-Range-Understanding.

    Light canonicalization (lowercase + trim + collapse whitespace) only — NO
    substring tolerance and NO article/punctuation stripping that would mask a
    wrong label. The parsed prediction must EQUAL the gold: ``"label: 43"`` is
    WRONG against gold ``"43"`` (the paper notes EM parsing is strict). Empty
    gold matches only an empty prediction.
    """
    g = _canon_strict(gold)
    p = _canon_strict(prediction)
    if not g:
        return not p
    return g == p


def _canon_strict(text: str) -> str:
    """Lowercase + trim + whitespace-collapse, preserving punctuation/labels."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text.strip().lower())


def _score_question(
    competency: str,
    prediction: str,
    golds: Sequence[str],
    *,
    choices: Optional[Sequence[str]] = None,
) -> float:
    """Per-question 0/1 score: SubEM for AR/CR, EM for TTL/LRU; max over golds.

    ``choices`` is accepted for MCQ-style ICL/LRU subsets; an EM competency with
    choices still scores by strict EM against the gold option string (the gold
    list already carries the correct option text), which is the strict-match the
    paper specifies. Unknown competencies fall back to SubEM (the looser, never-
    over-crediting-an-EM-label choice).
    """
    metric = em if competency in _EM_COMPETENCIES else sub_em
    if not golds:
        return 0.0
    return 1.0 if any(metric(prediction, g) for g in golds) else 0.0


def _acceptable_golds(task: Task) -> list[str]:
    """All acceptable gold strings for a task (``acceptable_answers`` ∪ answer).

    The loader stores the full ``answers[i]`` list under
    ``metadata['acceptable_answers']`` and the first as ``Task.answer``. Fixtures
    that omit ``acceptable_answers`` (e.g. the bundled offline fixture) degrade
    to just ``Task.answer``. De-duplicated, order-preserving, empties dropped.
    """
    out: list[str] = []
    seen: set[str] = set()
    raw = task.metadata.get("acceptable_answers")
    candidates: list[Any] = []
    if isinstance(raw, (list, tuple)):
        candidates.extend(raw)
    if task.answer is not None:
        candidates.append(task.answer)
    for c in candidates:
        if c is None:
            continue
        s = str(c)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Chunking (the "inject once" stream)
# --------------------------------------------------------------------------- #
def _estimate_tokens(text: str) -> int:
    """Whitespace-word token estimate (``words * 1.3``), matching the harness."""
    if not text or not text.strip():
        return 0
    return max(1, int(round(len(text.split()) * _WORDS_TO_TOKENS)))


def rechunk_sessions(
    sessions: Sequence[Session], *, chunk_tokens: int, task_id: str
) -> list[Session]:
    """Re-split a task's sessions into sequential ~``chunk_tokens``-sized chunks.

    Concatenates the sessions' content (preserving order) and re-emits fixed-size
    sequential chunks, each a fresh :class:`~memeval.schema.Session` carrying its
    in-stream ``index`` and a synthetic ``session_id``. Timestamps are inherited
    from the nearest source session so the recency metric stays meaningful. When
    the existing sessions already fit the chunk budget (the common offline-fixture
    case: short pre-chunked sessions), they are returned UNCHANGED so the
    deterministic per-session memory items the offline path relies on are
    preserved. This is the native "split into 512/4096-token chunks" step (b) —
    additive and offline-safe.
    """
    if chunk_tokens <= 0 or not sessions:
        return list(sessions)
    # If every existing session is already within budget, keep them as-is: the
    # loader's pre-chunked sessions are the natural ingest units and re-merging
    # them would only blur provenance. Re-chunk only when a session overflows.
    if all(_estimate_tokens(s.content) <= chunk_tokens for s in sessions):
        return list(sessions)

    budget_words = max(1, int(chunk_tokens / _WORDS_TO_TOKENS))
    chunks: list[Session] = []
    cur_words: list[str] = []
    cur_ts = sessions[0].timestamp
    idx = 0

    def _flush() -> None:
        nonlocal cur_words, idx
        if not cur_words:
            return
        chunks.append(
            Session(
                session_id=f"{task_id}::chunk{idx}",
                content=" ".join(cur_words),
                timestamp=cur_ts,
                index=idx,
                role="user",
                metadata={"chunk_tokens": chunk_tokens},
            )
        )
        idx += 1
        cur_words = []

    for sess in sessions:
        cur_ts = sess.timestamp or cur_ts
        for word in sess.content.split():
            cur_words.append(word)
            if len(cur_words) >= budget_words:
                _flush()
    _flush()
    return chunks or list(sessions)


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #
class MemoryAgentBenchNativeEvaluator(BaseNativeEvaluator):
    """Native two-phase evaluator for MemoryAgentBench's four competencies."""

    benchmark = Benchmark.MEMORY_AGENT_BENCH.value

    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        mode: str = "off",
        store: Any = None,
        judge: Any = None,  # noqa: ARG002 - accepted for symmetry; not needed
        cost: Any = None,
        limit: Any = None,  # noqa: ARG002 - already applied by the runner
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Ingest each block's chunk stream, then answer its questions.

        ``chunk_tokens`` selects the 512/4096 chunk-size configuration (default
        512). ``k`` is the retrieval depth. Memory is per-task (a block is one
        Task post-loader), built fresh and not carried across tasks — so we drive
        :meth:`run_tasks` over a per-task store, having first re-chunked each
        task's sessions to the configured size.
        """
        chunk_tokens = int(kwargs.get("chunk_tokens") or _DEFAULT_CHUNK_TOKENS)
        memory = mode_to_memory(mode)

        # Step (a): re-chunk each task's sessions to the configured token size.
        # We rebuild lightweight Task views sharing all other fields so the
        # frozen loader output is never mutated.
        rechunked: list[Task] = []
        for t in tasks:
            new_sessions = rechunk_sessions(
                t.sessions, chunk_tokens=chunk_tokens, task_id=t.task_id
            )
            if new_sessions is t.sessions or new_sessions == list(t.sessions):
                rechunked.append(t)
            else:
                rechunked.append(_with_sessions(t, new_sessions))

        # Steps (b)+(c): ingest the stream + answer each question. The reuse seam
        # writes each session/chunk into a per-task InMemoryStore, retrieves, and
        # has EchoModel surface the answer — fully offline + deterministic.
        records = self.run_tasks(
            rechunked,
            agent_or_model=agent_or_model,
            memory=memory,
            store=store,
            cost=cost,
            k=int(kwargs.get("k", 5)),
        )
        # Stash the chunk-size config so score() / metadata can report it.
        for r in records:
            r.extra["chunk_tokens"] = chunk_tokens
        return records

    def score(
        self,
        records: Sequence[PerTaskRecord],
        tasks: Sequence[Task],
    ) -> BenchmarkNativeReport:
        """Fold records into per-competency SubEM/EM means + an overall mean.

        Pure + deterministic. Each record is scored with its competency's metric
        (SubEM for AR/CR, EM for TTL/LRU) against the task's acceptable golds; the
        four competency means become components and the unweighted mean of those
        four becomes the headline ``per_competency_mean_accuracy``. A flat
        question-level micro accuracy is also reported for reference.
        """
        by_id = {t.task_id: t for t in tasks}
        chunk_tokens = (
            records[0].extra.get("chunk_tokens", _DEFAULT_CHUNK_TOKENS)
            if records
            else _DEFAULT_CHUNK_TOKENS
        )
        rep = self.empty_report(
            mode="",  # filled by the runner from its mode arg
            n_tasks=len(records),
            chunk_tokens=chunk_tokens,
            paper_chunk_sizes=list(_PAPER_CHUNK_SIZES),
            competencies=list(_COMPONENT_ORDER),
            sources=[
                "https://arxiv.org/abs/2507.05257",
                "https://github.com/HUST-AI-HYZ/MemoryAgentBench",
            ],
        )

        # Group per-question scores by competency.
        per_comp_scores: dict[str, list[float]] = {c: [] for c in _COMPONENT_ORDER}
        per_comp_subset: dict[str, dict[str, list[float]]] = {
            c: {} for c in _COMPONENT_ORDER
        }
        all_scores: list[float] = []

        for r in records:
            task = by_id.get(r.task_id)
            if task is None:
                continue
            comp = task.competency or task.stratum()
            golds = _acceptable_golds(task)
            s = _score_question(
                comp, r.prediction, golds, choices=task.choices
            )
            all_scores.append(s)
            per_comp_scores.setdefault(comp, []).append(s)
            # Sub-label breakdown (fact_sh/fact_mh for CR; ICL_* for TTL, etc.).
            subset = _subset_label(task)
            if subset:
                per_comp_subset.setdefault(comp, {}).setdefault(subset, []).append(s)

        # Per-competency components + their metric.
        comp_means: list[float] = []
        for comp in _present_competencies(per_comp_scores):
            scores = per_comp_scores.get(comp, [])
            if not scores:
                continue
            metric_name = "sub_em" if comp in _SUBEM_COMPETENCIES else "exact_match"
            value = mean(scores)
            cs = ComponentScore(name=comp, n=len(scores))
            cs.add(
                NativeMetric(
                    name=metric_name,
                    value=value,
                    n=len(scores),
                    better="higher",
                    metadata={"metric": metric_name},
                )
            )
            # Optional sub-rows (e.g. fact_sh vs fact_mh, per-ICL subset).
            for sub, sub_scores in sorted(per_comp_subset.get(comp, {}).items()):
                cs.add(
                    NativeMetric(
                        name=f"{metric_name}:{sub}",
                        value=mean(sub_scores),
                        n=len(sub_scores),
                        better="higher",
                        metadata={"subset": sub},
                    )
                )
            rep.add_component(cs)
            comp_means.append(value)

        # Headline: unweighted mean of the present competencies' means (the paper
        # presents per-competency rows; the overall is their simple average).
        rep.add_metric(
            NativeMetric(
                name="per_competency_mean_accuracy",
                value=mean(comp_means),
                n=len(comp_means),
                better="higher",
                metadata={"aggregation": "unweighted mean of competency means"},
            )
        )
        # Reference micro accuracy over all questions (not the headline).
        rep.add_metric(
            NativeMetric(
                name="question_accuracy_micro",
                value=mean(all_scores),
                n=len(all_scores),
                better="higher",
                metadata={"note": "flat per-question mean across all competencies"},
            )
        )
        return rep


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _subset_label(task: Task) -> Optional[str]:
    """The raw split / sub-label for breakdown rows, if present.

    Prefers ``metadata['subset']`` (the loader stores the raw split there);
    otherwise ``None``. Lowercased+canonicalized for stable keys.
    """
    sub = task.metadata.get("subset")
    if not sub:
        return None
    return str(sub).strip().lower().replace("-", "_").replace(" ", "_")


def _present_competencies(scores: dict[str, list[float]]) -> list[str]:
    """Competencies to report: the four canonical ones first (in order), then
    any extra/unknown competency keys that actually have scores, sorted."""
    ordered = [c for c in _COMPONENT_ORDER if scores.get(c)]
    extras = sorted(
        c for c, v in scores.items() if v and c not in _COMPONENT_ORDER
    )
    return ordered + extras


def _with_sessions(task: Task, sessions: list[Session]) -> Task:
    """Return a shallow copy of ``task`` with replaced ``sessions``.

    Never mutates the loader's Task. Copies every field so grouping/metadata/gold
    survive the re-chunk; only ``sessions`` change.
    """
    return Task(
        task_id=task.task_id,
        benchmark=task.benchmark,
        kind=task.kind,
        question=task.question,
        answer=task.answer,
        choices=list(task.choices) if task.choices else task.choices,
        sessions=sessions,
        gold_memory_ids=list(task.gold_memory_ids),
        group_id=task.group_id,
        order=task.order,
        repo=task.repo,
        base_commit=task.base_commit,
        patch=task.patch,
        test_patch=task.test_patch,
        fail_to_pass=list(task.fail_to_pass),
        pass_to_pass=list(task.pass_to_pass),
        competency=task.competency,
        metadata=dict(task.metadata),
    )


__all__ = [
    "MemoryAgentBenchNativeEvaluator",
    "sub_em",
    "em",
    "rechunk_sessions",
]
