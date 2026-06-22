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
* ``qa_accuracy_overall`` — primary metric: fraction the judge labels correct.
  Native judge is GPT-4o (``"yes" in response.lower()``); offline we substitute
  the stdlib :class:`~memeval.native.judge.DeterministicJudge`. Per the official
  scorer, the **abstention** cohort is graded with a DISTINCT unanswerable-
  question prompt; the answerable cohort with the per-question-type prompt
  (single-session-preference uses the preference/rubric prompt). The headline
  accuracy is the mean over ALL questions (answerable + abstention), exactly as
  ``print_qa_metrics.py`` reports it.
* ``qa_accuracy_by_question_type`` — the headline breakdown: accuracy within each
  of the six native answerable types, exposed as one
  :class:`~memeval.native.spec.ComponentScore` per type.
* ``abstention_accuracy`` — accuracy on the ``_abs`` cohort, scored with the
  distinct unanswerable judge prompt and reported separately (its own component,
  NOT a normal question-type bucket).
* ``answer_session_recall_at_k`` + ``answer_session_ndcg_at_k`` — session-level
  retrieval quality. ``recall_all@k`` = fraction of a question's gold evidence
  sessions (``answer_session_ids`` -> ``Task.gold_memory_ids``) appearing in the
  top-k retrieved sessions; ``ndcg_any@k`` = NDCG@k over the ranked retrieved
  list with binary relevance (rel=1 if the session is gold). Both are means over
  the NON-abstention cohort (abstention items have no ground-truth location and
  are skipped, matching ``print_retrieval_metrics.py``). Native k is {5,10,50};
  we additionally report {1,3} since an offline retriever may return few items.
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
from .base import (
    BaseNativeEvaluator,
    mean,
    mode_to_memory,
    ndcg_at_k,
    set_recall,
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
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Run each (question, full-history) trial once → per-task records.

        Each LongMemEval question is an independent trial with its own history
        (no carry-over across questions), so we drive the EXACT loaded task list
        through the shared agent seam via :meth:`BaseNativeEvaluator.run_tasks`
        (EchoAgent offline; whatever AgentAdapter the caller passes online). For
        each resulting trajectory we cache the judge label in ``extra`` so
        :meth:`score` stays pure (no judge/model call there). The judge ``kind``
        is chosen per question: ``"abstention"`` for ``_abs`` items, the
        ``"preference"`` rubric prompt for single-session-preference, else
        ``"qa"`` — exactly the three prompt families the official
        ``get_anscheck_prompt`` switches between.
        """
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
        all_labels: list[float] = []
        abst_labels: list[float] = []
        by_type: dict[str, list[float]] = {t: [] for t in _QUESTION_TYPES}
        for rec in records:
            label = 1.0 if rec.extra.get("label") else 0.0
            all_labels.append(label)
            task = by_id.get(rec.task_id)
            is_abs = bool(rec.extra.get("abstention")) or (
                task is not None and bool(task.metadata.get("abstention"))
            )
            if is_abs:
                abst_labels.append(label)
                continue
            qtype = rec.extra.get("question_type") or (
                task.competency if task is not None else ""
            )
            if qtype in by_type:
                by_type[qtype].append(label)
            else:
                by_type.setdefault(qtype or "unknown", []).append(label)

        rep.add_metric(
            NativeMetric(
                "qa_accuracy_overall",
                mean(all_labels),
                n=len(all_labels),
                better="higher",
                metadata={"label_parse": "'yes' in judge_response.lower()"},
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

        # Abstention as its own component (NOT a normal question-type bucket).
        abst_comp = ComponentScore(
            name="abstention",
            n=len(abst_labels),
            metadata={"selector": "question_id endswith '_abs'"},
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
        """Append ``answer_session_recall@k`` / ``answer_session_ndcg@k``.

        Session-level only (``answer_session_ids`` == ``Task.gold_memory_ids``).
        Abstention items are skipped (no ground-truth location), matching the
        native ``print_retrieval_metrics.py``. ``recall_all@k`` averages the
        fraction of a question's gold sessions in the top-k retrieved ids;
        ``ndcg_any@k`` averages NDCG@k over the ranked retrieved list with binary
        gold relevance. Questions with no gold sessions are excluded from the
        recall/NDCG means (their recall is undefined).
        """
        recall_at: dict[int, list[float]] = {k: [] for k in _RECALL_KS}
        ndcg_at: dict[int, list[float]] = {k: [] for k in _RECALL_KS}
        n_scored = 0
        for rec in records:
            task = by_id.get(rec.task_id)
            if task is None:
                continue
            if rec.extra.get("abstention") or bool(task.metadata.get("abstention")):
                continue
            gold = {str(g) for g in task.gold_memory_ids}
            if not gold:
                # No ground-truth evidence location: nothing to score.
                continue
            ranked_ids = _ranked_session_ids(rec)
            n_scored += 1
            for k in _RECALL_KS:
                topk = ranked_ids[:k]
                recall_at[k].append(set_recall(set(topk), gold))
                rels = [1.0 if sid in gold else 0.0 for sid in topk]
                ndcg_at[k].append(ndcg_at_k(rels, k))

        for k in _RECALL_KS:
            vals = recall_at[k]
            rep.add_metric(
                NativeMetric(
                    f"answer_session_recall_at_{k}",
                    mean(vals),
                    n=len(vals),
                    better="higher",
                    metadata={"k": k, "level": "session", "variant": "recall_all"},
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
                    metadata={"k": k, "level": "session", "variant": "ndcg_any"},
                )
            )
        rep.metadata.setdefault("n_retrieval_scored", n_scored)


# --------------------------------------------------------------------------- #
# Module-level helpers (stdlib only)
# --------------------------------------------------------------------------- #
def _judge_kind(task: Task) -> str:
    """Pick the judge prompt family for a task (mirrors get_anscheck_prompt).

    ``"abstention"`` for the ``_abs`` cohort; ``"preference"`` for
    single-session-preference (its gold is a rubric); ``"qa"`` otherwise.
    """
    if bool(task.metadata.get("abstention")) or task.task_id.endswith("_abs"):
        return "abstention"
    if (task.competency or "") in _PREFERENCE_TYPES:
        return "preference"
    return "qa"


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
