"""LongMemEval native evaluator (arXiv 2410.10813, ICLR 2025).

LongMemEval is a **single-turn QA** benchmark over long, multi-session chat
histories (500 questions, ~30 of them abstention). Each question is an
INDEPENDENT trial: the system is shown *that question's own* timestamped chat
history (the parallel ``haystack_sessions`` / ``haystack_dates`` /
``haystack_session_ids`` arrays, which the loader has already normalized into
:class:`~memeval.schema.Session` objects in chronological order) plus the
question and its ``question_date`` (the recency reference / "now"), and must emit
exactly one answer (the *hypothesis*). NO memory or context is carried across
questions — questions are not sequential/continual (contrast SWE-Bench-CL). The
official pipeline is index/retrieve → read/generate → grade; we mirror the
read/generate + grade stages and additionally report session-level retrieval
recall/NDCG from the retrieve step the agent logged.

What we compute (matching the official ``evaluate_qa.py`` /
``print_qa_metrics.py`` / ``print_retrieval_metrics.py``)
---------------------------------------------------------
* ``qa_accuracy_overall`` — primary metric (the official "Overall Accuracy",
  ``np.mean(all_acc)``): the MICRO mean of the judge label over ALL questions
  (answerable + abstention), exactly as ``print_qa_metrics.py`` reports it.
  Native judge is GPT-4o (``"yes" in response.lower()``); offline we substitute
  the stdlib :class:`~memeval.native.judge.DeterministicJudge`. Per the official
  scorer, the **abstention** cohort is graded with a DISTINCT unanswerable-
  question prompt; the answerable cohort with the per-question-type prompt
  (single-session-preference uses the preference/rubric prompt).
* ``qa_accuracy_task_averaged`` — the official "Task-averaged Accuracy"
  (``np.mean(task_acc)``): the MACRO mean over the per-question-type accuracies.
  ``print_qa_metrics.py`` prints BOTH this and Overall Accuracy, so we surface
  both. Computed over exactly the per-type buckets used for the breakdown below
  (so it matches the official macro number, including ``_abs`` items in their
  real type — see next bullet).
* ``qa_accuracy_by_question_type`` — the headline breakdown: accuracy within each
  native question type, exposed as one
  :class:`~memeval.native.spec.ComponentScore` per type. CRITICAL fidelity
  detail: ``print_qa_metrics.py`` appends EVERY entry to its
  ``question_type`` bucket UNCONDITIONALLY — including ``_abs`` items — *before*
  the separate abstention tally. So an ``_abs`` item is counted BOTH in its real
  question type AND in ``abstention_acc`` (double-counted by design). We mirror
  that: ``_abs`` items contribute to their ``question_type`` bucket here too.
* ``abstention_accuracy`` — accuracy on the ``_abs`` cohort (selector
  ``'_abs' in question_id``, matching the official substring test), scored with
  the distinct unanswerable judge prompt and reported separately (its own
  component AND headline metric), in addition to (not instead of) its type
  bucket.
* ``answer_session_recall_at_k`` + ``answer_session_ndcg_at_k`` — session-level
  retrieval quality, faithful to ``eval_utils.py``. ``recall_all@k`` is
  ALL-OR-NOTHING per question: ``1.0`` iff EVERY gold evidence session
  (``answer_session_ids`` -> ``Task.gold_memory_ids``) is in the top-k retrieved
  sessions, else ``0.0`` (``float(all(doc in topk for doc in gold))``). We ALSO
  report ``recall_any@k`` = ``float(any(...))`` (1.0 iff at least one gold session
  is in top-k). ``ndcg_any@k`` = NDCG@k over the ranked retrieved list with binary
  relevance, using LongMemEval's idiosyncratic DCG (``rel[0] +
  sum(rel[1:]/log2(arange(2,n+1)))`` — positions 0 AND 1 share the same
  discount), not the textbook ``rel/log2(i+2)``. All are means over the
  NON-abstention cohort (abstention items have no ground-truth location and are
  skipped, matching ``print_retrieval_metrics.py``). Native k is {5,10,50}; we
  additionally report {1,3} since an offline retriever may return few items.
  Turn-level recall (``has_answer``) is intentionally NOT reported: the loader
  does not preserve per-turn ``has_answer``, so only the session-level variant is
  faithfully derivable from the normalized :class:`~memeval.schema.Task`.

Both phases are stdlib-only and import cleanly; the live judge (and any live
agent) are reached only when the caller passes them in. The offline default
(EchoAgent + EchoModel + InMemoryStore + DeterministicJudge) is fully
deterministic with no network/LLM.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ...schema import Benchmark, Task
from ..judge import DeterministicJudge, Judge
from ..spec import (
    BenchmarkNativeReport,
    ComponentScore,
    NativeMetric,
    PerTaskRecord,
)
import math

from .base import (
    BaseNativeEvaluator,
    mean,
    mode_to_memory,
)

#: Retrieval cut-offs we report recall/NDCG at. Native LongMemEval reports
#: {5,10,50}; we add {1,3} because an offline retriever often returns fewer
#: items than 50 (the metric still degrades gracefully — recall@50 just equals
#: recall over whatever was retrieved).
_RECALL_KS: tuple[int, ...] = (1, 3, 5, 10, 50)

#: The six native ANSWERABLE question types (the reportable breakdown), in the
#: order LongMemEval reports them. Stored in the loader's normalized form
#: (lowercase, hyphens/spaces -> underscores) so they match ``Task.competency``.
_QUESTION_TYPES: tuple[str, ...] = (
    "single_session_user",
    "single_session_assistant",
    "single_session_preference",
    "multi_session",
    "temporal_reasoning",
    "knowledge_update",
)

#: Native (hyphenated) display names keyed by the normalized competency, so the
#: report components read exactly as the paper labels them.
_DISPLAY_NAME: dict[str, str] = {
    "single_session_user": "single-session-user",
    "single_session_assistant": "single-session-assistant",
    "single_session_preference": "single-session-preference",
    "multi_session": "multi-session",
    "temporal_reasoning": "temporal-reasoning",
    "knowledge_update": "knowledge-update",
}

#: Types whose native grader uses the PREFERENCE (rubric-overlap) prompt rather
#: than the plain QA prompt. (single-session-preference's gold is a rubric.)
_PREFERENCE_TYPES = frozenset({"single_session_preference"})

#: Competencies whose official ``get_anscheck_prompt`` branch carries DISTINCT
#: grading wording (so the live judge must use the matching template). Mapped to
#: the judge ``kind``. The offline DeterministicJudge scores these as plain QA —
#: the nuance (temporal off-by-one tolerance; knowledge-update "latest answer is
#: correct") lives in the LLM prompt only, so offline scoring is unaffected.
_DISTINCT_QA_KINDS = frozenset({"temporal_reasoning", "knowledge_update"})


class LongMemEvalNativeEvaluator(BaseNativeEvaluator):
    """Native evaluator for LongMemEval QA (judge-graded, retrieval-aware)."""

    benchmark = Benchmark.LONGMEMEVAL.value

    # ------------------------------------------------------------------ #
    # run: drive the agent once per question, cache a judge label per record
    # ------------------------------------------------------------------ #
    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        mode: str = "off",
        store: Any = None,
        judge: Optional[Judge] = None,
        cost: Any = None,
        limit: Optional[int] = None,
        k: int = 5,
        allow_shared_store: bool = False,
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Run each (question, full-history) trial once → per-task records.

        Each LongMemEval question is an INDEPENDENT trial with its own history
        (no carry-over across questions), so we drive the EXACT loaded task list
        through the shared agent seam via :meth:`BaseNativeEvaluator.run_tasks`
        (EchoAgent offline; whatever AgentAdapter the caller passes online). For
        each resulting trajectory we cache the judge label in ``extra`` so
        :meth:`score` stays pure (no judge/model call there). The judge ``kind``
        is chosen per question: ``"abstention"`` for ``_abs`` items, the
        ``"preference"`` rubric prompt for single-session-preference, else
        ``"qa"`` — exactly the three prompt families the official
        ``get_anscheck_prompt`` switches between.

        Independent-trial guard
        -----------------------
        ``_store_for_task`` returns an explicit ``store`` for ALL tasks, which
        would leak one question's history into the next and violate LongMemEval's
        independent-trial protocol (each question must see ONLY its own
        ``haystack_sessions``). The default path (offline + ``run_native``) passes
        ``store=None`` so every task gets a FRESH per-task ``InMemoryStore`` — the
        correct behavior. To prevent accidental misuse we REJECT a non-``None``
        ``store`` here unless the caller explicitly opts in with
        ``allow_shared_store=True`` (e.g. a custom store that is itself reset
        per task). LongMemEval tasks carry no ``group_id``, so with ``store=None``
        no cross-task carry-over can occur.
        """
        if store is not None and not allow_shared_store:
            raise ValueError(
                "LongMemEval is an independent-trial benchmark: a shared `store` "
                "would leak memory across questions (each question must see only "
                "its own haystack). Pass store=None (the default, fresh per-task "
                "InMemoryStore) or, only if your store resets per task, "
                "allow_shared_store=True."
            )
        records = self.run_tasks(
            tasks,
            agent_or_model=agent_or_model,
            memory=mode_to_memory(mode),
            store=store,
            cost=cost,
            k=k,
        )
        j: Judge = judge or DeterministicJudge()
        by_id = {t.task_id: t for t in tasks}
        for rec in records:
            task = by_id.get(rec.task_id)
            if task is None:  # pragma: no cover - run_tasks preserves ids
                continue
            kind = _judge_kind(task)
            gold = task.answer or ""
            rec.extra["judge_kind"] = kind
            rec.extra["abstention"] = bool(task.metadata.get("abstention"))
            rec.extra["question_type"] = task.competency or ""
            rec.extra["label"] = bool(
                j.judge(task.question, gold, rec.prediction, kind=kind)
            )
        return records

    # ------------------------------------------------------------------ #
    # score: pure fold of cached labels + retrieve steps into the report
    # ------------------------------------------------------------------ #
    def score(
        self,
        records: Sequence[PerTaskRecord],
        tasks: Sequence[Task],
    ) -> BenchmarkNativeReport:
        """Fold cached judge labels + retrieve steps → the native report.

        Pure/deterministic: reads each record's cached ``extra["label"]`` (set by
        :meth:`run`) and the retrieve step on its trajectory; never calls a model
        or judge. Produces the headline ``qa_accuracy_overall`` /
        ``abstention_accuracy`` / ``answer_session_recall@k`` /
        ``answer_session_ndcg@k`` metrics plus one component per question type and
        one for the abstention cohort.
        """
        by_id = {t.task_id: t for t in tasks}
        rep = self.empty_report(
            mode=_mode_of(records),
            n_tasks=len(records),
            paper="arXiv:2410.10813",
            sources=[
                "https://arxiv.org/abs/2410.10813",
                "https://github.com/xiaowu0162/LongMemEval",
            ],
            recall_ks=list(_RECALL_KS),
        )

        # ---- accuracy (overall, by type, abstention) -------------------- #
        # Faithful to print_qa_metrics.py: EVERY entry is appended to its
        # question_type bucket UNCONDITIONALLY (including ``_abs`` items, in their
        # real type), and a SEPARATE pass tallies the ``_abs`` cohort into
        # ``abstention_acc``. So an ``_abs`` item is double-counted by design (its
        # type bucket AND abstention). ``Overall Accuracy`` is the micro mean over
        # all entries; ``Task-averaged Accuracy`` is the macro mean of the per-type
        # accuracies.
        all_labels: list[float] = []
        abst_labels: list[float] = []
        by_type: dict[str, list[float]] = {t: [] for t in _QUESTION_TYPES}
        for rec in records:
            label = 1.0 if rec.extra.get("label") else 0.0
            all_labels.append(label)
            task = by_id.get(rec.task_id)
            # Question type bucket: append EVERY item (incl. _abs) to its real
            # type, matching the official unconditional `type2acc[...].append(...)`.
            qtype = rec.extra.get("question_type") or (
                task.competency if task is not None else ""
            )
            if qtype in by_type:
                by_type[qtype].append(label)
            else:
                by_type.setdefault(qtype or "unknown", []).append(label)
            # Abstention tally: official uses substring `'_abs' in question_id`.
            if _is_abstention(rec, task):
                abst_labels.append(label)

        rep.add_metric(
            NativeMetric(
                "qa_accuracy_overall",
                mean(all_labels),
                n=len(all_labels),
                better="higher",
                metadata={
                    "label_parse": "'yes' in judge_response.lower()",
                    "official_name": "Overall Accuracy (micro: np.mean(all_acc))",
                },
            )
        )
        # Task-averaged (macro) Accuracy: np.mean over the per-type means. The
        # official scorer averages over every question_type present in the data
        # (each type weighted equally regardless of its item count).
        type_means = [
            mean(labels) for labels in by_type.values() if labels
        ]
        rep.add_metric(
            NativeMetric(
                "qa_accuracy_task_averaged",
                mean(type_means),
                n=len(type_means),
                better="higher",
                metadata={
                    "official_name": "Task-averaged Accuracy (macro: np.mean(task_acc))",
                    "note": "mean over per-question-type accuracies; _abs items "
                            "counted in their real type (as print_qa_metrics.py does)",
                },
            )
        )
        rep.add_metric(
            NativeMetric(
                "abstention_accuracy",
                mean(abst_labels),
                n=len(abst_labels),
                better="higher",
                metadata={"cohort": "_abs (distinct unanswerable judge prompt)"},
            )
        )

        # Per-question-type component (the headline LongMemEval breakdown).
        for qtype in _QUESTION_TYPES:
            labels = by_type.get(qtype, [])
            display = _DISPLAY_NAME.get(qtype, qtype)
            comp = ComponentScore(name=display, n=len(labels))
            comp.add(
                NativeMetric(
                    "qa_accuracy",
                    mean(labels),
                    n=len(labels),
                    better="higher",
                )
            )
            rep.add_component(comp)
        # Surface any unexpected non-abstention type the data carried.
        for qtype, labels in by_type.items():
            if qtype in _QUESTION_TYPES or not labels:
                continue
            comp = ComponentScore(name=qtype, n=len(labels))
            comp.add(NativeMetric("qa_accuracy", mean(labels), n=len(labels)))
            rep.add_component(comp)

        # Abstention as its OWN dedicated component, in ADDITION to the type
        # buckets above (an _abs item appears in BOTH, exactly as the official
        # scorer double-counts it).
        abst_comp = ComponentScore(
            name="abstention",
            n=len(abst_labels),
            metadata={"selector": "'_abs' in question_id (official substring test)"},
        )
        abst_comp.add(
            NativeMetric(
                "abstention_accuracy",
                mean(abst_labels),
                n=len(abst_labels),
                better="higher",
            )
        )
        rep.add_component(abst_comp)

        # ---- session-level retrieval recall / NDCG (non-abstention) ----- #
        self._add_retrieval_metrics(rep, records, by_id)

        return rep

    # ------------------------------------------------------------------ #
    # retrieval scoring helpers
    # ------------------------------------------------------------------ #
    def _add_retrieval_metrics(
        self,
        rep: BenchmarkNativeReport,
        records: Sequence[PerTaskRecord],
        by_id: dict[str, Task],
    ) -> None:
        """Append ``answer_session_recall_{all,any}@k`` / ``answer_session_ndcg@k``.

        Session-level only (``answer_session_ids`` == ``Task.gold_memory_ids``),
        faithful to LongMemEval's ``eval_utils.py``. Abstention items are skipped
        (no ground-truth location), matching ``print_retrieval_metrics.py``.

        * ``recall_all@k`` is ALL-OR-NOTHING per question — ``1.0`` iff EVERY gold
          session is in the top-k (``float(all(doc in topk for doc in gold))``),
          NOT the fractional ``|topk ∩ gold| / |gold|``. For multi-gold questions
          (multi-session / temporal-reasoning) finding only some gold sessions
          scores ``0.0`` here.
        * ``recall_any@k`` is ``float(any(...))`` — ``1.0`` iff at least one gold
          session is in the top-k.
        * ``ndcg_any@k`` uses LongMemEval's idiosyncratic DCG (positions 0 and 1
          share the same discount), via :func:`_lme_ndcg`.

        Questions with no gold sessions are excluded from the means (recall is
        undefined there).
        """
        recall_all_at: dict[int, list[float]] = {k: [] for k in _RECALL_KS}
        recall_any_at: dict[int, list[float]] = {k: [] for k in _RECALL_KS}
        ndcg_at: dict[int, list[float]] = {k: [] for k in _RECALL_KS}
        n_scored = 0
        for rec in records:
            task = by_id.get(rec.task_id)
            if task is None:
                continue
            if _is_abstention(rec, task):
                continue
            gold = {str(g) for g in task.gold_memory_ids}
            if not gold:
                # No ground-truth evidence location: nothing to score.
                continue
            ranked_ids = _ranked_session_ids(rec)
            n_scored += 1
            for k in _RECALL_KS:
                topk = set(ranked_ids[:k])
                # All-or-nothing recall_all and binary recall_any, per eval_utils.
                recall_all_at[k].append(1.0 if gold <= topk else 0.0)
                recall_any_at[k].append(1.0 if (gold & topk) else 0.0)
                rels = [1.0 if sid in gold else 0.0 for sid in ranked_ids[:k]]
                ndcg_at[k].append(_lme_ndcg(rels, gold_count=len(gold)))

        for k in _RECALL_KS:
            vals = recall_all_at[k]
            rep.add_metric(
                NativeMetric(
                    f"answer_session_recall_at_{k}",
                    mean(vals),
                    n=len(vals),
                    better="higher",
                    metadata={
                        "k": k,
                        "level": "session",
                        "variant": "recall_all",
                        "formula": "float(all(g in topk for g in gold)) "
                                   "(all-or-nothing, per eval_utils.py)",
                    },
                )
            )
        for k in _RECALL_KS:
            vals = recall_any_at[k]
            rep.add_metric(
                NativeMetric(
                    f"answer_session_recall_any_at_{k}",
                    mean(vals),
                    n=len(vals),
                    better="higher",
                    metadata={
                        "k": k,
                        "level": "session",
                        "variant": "recall_any",
                        "formula": "float(any(g in topk for g in gold))",
                    },
                )
            )
        for k in _RECALL_KS:
            vals = ndcg_at[k]
            rep.add_metric(
                NativeMetric(
                    f"answer_session_ndcg_at_{k}",
                    mean(vals),
                    n=len(vals),
                    better="higher",
                    metadata={
                        "k": k,
                        "level": "session",
                        "variant": "ndcg_any",
                        "dcg": "rel[0] + sum(rel[1:]/log2(arange(2,n+1))) "
                               "(LongMemEval eval_utils.py idiosyncratic DCG)",
                    },
                )
            )
        rep.metadata.setdefault("n_retrieval_scored", n_scored)


# --------------------------------------------------------------------------- #
# Module-level helpers (stdlib only)
# --------------------------------------------------------------------------- #
def _judge_kind(task: Task) -> str:
    """Pick the judge prompt family for a task (mirrors get_anscheck_prompt).

    Maps each task to the judge ``kind`` so the LIVE judge uses the matching
    official prompt branch:

    * ``"abstention"`` for the ``_abs`` cohort (official substring check
      ``'_abs' in question_id``, plus the loader's normalized ``abstention``
      flag);
    * ``"preference"`` for single-session-preference (its gold is a rubric);
    * ``"temporal_reasoning"`` / ``"knowledge_update"`` — distinct QA prompts
      (off-by-one tolerance / updated-answer-is-correct). The offline
      DeterministicJudge scores these as plain QA;
    * ``"qa"`` otherwise.
    """
    if bool(task.metadata.get("abstention")) or "_abs" in task.task_id:
        return "abstention"
    comp = task.competency or ""
    if comp in _PREFERENCE_TYPES:
        return "preference"
    if comp in _DISTINCT_QA_KINDS:
        return comp
    return "qa"


def _is_abstention(rec: PerTaskRecord, task: Optional[Task]) -> bool:
    """Whether a record is an abstention item, by the OFFICIAL substring test.

    ``print_qa_metrics.py`` / ``evaluate_qa.py`` select the abstention cohort with
    ``'_abs' in entry['question_id']`` (substring, NOT endswith). We honor that,
    plus the loader's normalized ``abstention`` metadata flag and the per-record
    cache set by :meth:`LongMemEvalNativeEvaluator.run`.
    """
    if bool(rec.extra.get("abstention")):
        return True
    if task is None:
        return "_abs" in rec.task_id
    return bool(task.metadata.get("abstention")) or "_abs" in task.task_id


def _lme_ndcg(relevances: Sequence[float], *, gold_count: int) -> float:
    """NDCG using LongMemEval's idiosyncratic DCG (``eval_utils.py``).

    Official ``dcg(rel) = rel[0] + sum(rel[1:] / log2(arange(2, n+1)))`` — i.e.
    positions 0 AND 1 both get discount ``1`` (``1/log2(2) == 1``), differing from
    the textbook ``rel / log2(i + 2)`` for position 0. The ideal DCG ranks all
    ``gold_count`` relevant items first (binary relevance), capped at the length of
    ``relevances`` (the top-k window). Returns ``0.0`` when there is no gold.
    """
    rels = list(relevances)
    if not rels or gold_count <= 0:
        return 0.0
    dcg = _lme_dcg(rels)
    ideal = [1.0] * min(gold_count, len(rels))
    idcg = _lme_dcg(ideal)
    return (dcg / idcg) if idcg > 0.0 else 0.0


def _lme_dcg(relevances: Sequence[float]) -> float:
    """LongMemEval DCG: ``rel[0] + sum(rel[i]/log2(i+1) for i>=1)``.

    Mirrors ``eval_utils.py``'s ``relevances[0] + np.sum(relevances[1:] /
    np.log2(np.arange(2, n+1)))`` — for the i-th (0-based) item with ``i>=1`` the
    discount is ``log2(i+1)`` (so i=1 -> log2(2)=1, i=2 -> log2(3), ...).
    """
    rels = list(relevances)
    if not rels:
        return 0.0
    total = rels[0]
    for i in range(1, len(rels)):
        total += rels[i] / math.log2(i + 1)
    return total


def _ranked_session_ids(rec: PerTaskRecord) -> list[str]:
    """Top retrieve step's session ids in rank order (best-first), de-duped.

    LongMemEval scores retrieval against the surfaced ranking for the question;
    each trial is independent, so we read the LAST retrieve step of the
    trajectory (the agent's final declared retrieval for this question). Items
    are ordered by their ``rank`` (0 == top hit), and ``item_id`` equals the
    session id (the store keys session memories by ``session_id``).
    """
    steps = [s for s in rec.trajectory.steps if s.kind == "retrieve"]
    if not steps:
        return []
    last = steps[-1]
    ordered = sorted(last.retrieved, key=lambda ri: ri.rank)
    seen: set[str] = set()
    out: list[str] = []
    for ri in ordered:
        sid = str(ri.item_id)
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _mode_of(records: Sequence[PerTaskRecord]) -> str:
    """Infer the run mode for the report from the records' ``memory_on`` flag."""
    if not records:
        return "off"
    return "on" if any(r.memory_on for r in records) else "off"


__all__ = ["LongMemEvalNativeEvaluator"]
