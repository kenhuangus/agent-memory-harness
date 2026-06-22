"""Native evaluator for **SWE-ContextBench** (arXiv 2602.08316).

Paper: *"SWE Context Bench: A Benchmark for Context Learning in Coding"*
(HF dataset ``jiayuanz3/SWEContextBench``). This module implements the
benchmark's NATIVE run protocol + metrics on top of the frozen harness seam,
exactly as the contract brief prescribes — additive, stdlib-only, offline-safe.

What the paper actually measures (and what we reproduce)
------------------------------------------------------
SWE-ContextBench links a RELATED task pool (376) to an EXPERIENCE/base pool
(1,100) via a Relationship file. The loader already encodes this: every row is
one :class:`~memeval.schema.Task`; ``group_id`` is the shared-context group
(experience root), and ``order`` is the within-group position (``0`` =
experience/base, ``>0`` = related). The central finding is **context lift** —
does enabling cross-task context retrieved from the sibling experience task
improve resolution of the related task?

Two-phase order, NO implicit carryover
    Phase 1 runs the experience tasks (order 0) first to POPULATE the context
    pool; Phase 2 runs each related task (order>0) in a fresh environment whose
    ONLY cross-task signal is retrieval from the SAME ``group_id``. We realize
    this with the harness's per-``group_id`` :class:`InMemoryStore` policy
    (:func:`memeval.agent._store_for_task`): seeding the experience task's
    sessions + its write-back into the group store, then letting the related
    task retrieve from it. No agent state crosses groups; retrieval is the sole
    cross-task channel — matching the paper.

A/B context lift
    For the RELATED subset we run TWO passes over the same tasks — memory-ON
    (cross-task context enabled) and memory-OFF (no-context baseline) — and
    report ``context_lift = resolve_rate(on) - resolve_rate(off)`` (schema
    ``Metrics.accuracy_lift``). Records are tagged via
    :attr:`PerTaskRecord.memory_on`.

Native metrics implemented (see the per-benchmark spec)
    * ``resolve_rate``         — Resolved % (SWE-bench rule: all FAIL_TO_PASS pass
                                 AND all PASS_TO_PASS pass). Offline stand-in uses
                                 the overlap grader vs the gold patch / a graded
                                 ``Trajectory.success``; the real Docker grader is
                                 used only when explicitly passed in and degrades
                                 gracefully (never hard-fails offline).
    * ``context_lift``         — resolve-rate delta on the related subset.
    * ``match_rate@k``         — fraction of related tasks whose top-k retrieved
                                 contexts include the gold-linked sibling.
    * ``context_recall@k``     — recall of the gold sibling context ids into top-k.
    * ``localization_*``       — file / function / line correct-location % from
                                 unified-diff parsing (degrades to n=0 when the
                                 prediction is not a diff, i.e. offline echo).
    * ``efficiency``           — memory-token overhead ratio (LOWER better) plus
                                 avg tokens / tool-calls.

Components (slices)
    overall, experience_tasks, related_tasks, config:* (no_context / free_context
    / oracle_context / free_summary / oracle_summary), by_language, by_difficulty,
    retrieval_quality@k, localization_granularity.

Everything in :meth:`score` is pure + deterministic (no model, no network, no
wall-clock). :meth:`run` is the only model-touching phase and is fully offline
with EchoAgent + per-group InMemoryStore.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ...grader import overlap_grader
from ...schema import Benchmark, Task, Trajectory
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
    set_recall,
)

#: Top-k cutoffs the paper reports retrieval "Matched %" at (Table 5).
_DEFAULT_KS: tuple[int, ...] = (1, 2, 3)

#: The five paper configurations. Each maps to (memory_on?, oracle?, granularity).
#: ``oracle`` forces retrieval to the gold sibling; ``granularity`` selects the
#: full trajectory vs the compact summary pool. The offline harness exposes the
#: sibling context as a retrievable session either way, so the configs differ in
#: how we *score* (oracle restricts the candidate gold set to the linked sibling).
_CONFIGS: dict[str, dict[str, Any]] = {
    "config:no_context": {"memory": False, "oracle": False, "granularity": None},
    "config:free_context": {"memory": True, "oracle": False, "granularity": "trajectory"},
    "config:oracle_context": {"memory": True, "oracle": True, "granularity": "trajectory"},
    "config:free_summary": {"memory": True, "oracle": False, "granularity": "summary"},
    "config:oracle_summary": {"memory": True, "oracle": True, "granularity": "summary"},
}


class SWEContextBenchNativeEvaluator(BaseNativeEvaluator):
    """Native SWE-ContextBench evaluator (context-learning resolve lift).

    Subclasses :class:`BaseNativeEvaluator` for the offline run seam
    (``run_tasks`` over EchoAgent + per-group InMemoryStore) and implements the
    A/B run + the paper's native scoring in :meth:`score`.
    """

    benchmark: str = Benchmark.SWE_CONTEXTBENCH.value

    # ------------------------------------------------------------------ #
    # run() — two-phase, A/B over the related subset
    # ------------------------------------------------------------------ #
    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        mode: str = "off",
        store: Any = None,
        judge: Any = None,  # unused: CODE benchmark, no LLM judge needed
        cost: Any = None,
        limit: Optional[int] = None,
        k: int = 3,
        grader: Any = None,
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Drive the agent over ``tasks`` and return per-task records.

        Ordering: tasks are sorted by ``(group_id, order)`` so each group's
        experience task (order 0) runs BEFORE its related siblings — the
        two-phase order the paper requires for the context pool to be populated
        before the related task retrieves from it. The per-``group_id``
        InMemoryStore policy keeps retrieval scoped to siblings (no cross-group
        carryover).

        Two passes are produced for EVERY task:

        * memory-ON  (``mode``-driven, default the cross-task context channel),
        * memory-OFF (the no-context baseline),

        tagged via :attr:`PerTaskRecord.memory_on`. The related subset's lift is
        the contrast between the two; the experience subset's OFF pass is its
        baseline difficulty. The offline grader is the dependency-free overlap
        stand-in (``overlap_grader``) unless a real grader is supplied; a real
        Docker grader is honored if passed but never required.
        """
        ordered = self._ordered(tasks)
        mem_default = mode_to_memory(mode)
        # Offline default grader: token-overlap vs gold patch. A caller may pass
        # the real SWEBenchDockerGrader; if Docker is unavailable it returns None
        # (ungraded) and we fall back per-task, so we NEVER hard-fail offline.
        offline_grader = grader if grader is not None else overlap_grader

        records: list[PerTaskRecord] = []
        # Memory-ON pass (cross-task context enabled). Honor the requested mode;
        # if mode is "off" we still emit an ON pass so lift is always computable.
        records.extend(
            self._tag(
                self.run_tasks(
                    ordered, agent_or_model=agent_or_model, memory=True,
                    store=store, cost=cost, grader=offline_grader, k=k,
                ),
                memory_on=True,
            )
        )
        # Memory-OFF baseline pass (no cross-task context).
        records.extend(
            self._tag(
                self.run_tasks(
                    ordered, agent_or_model=agent_or_model, memory=False,
                    store=store, cost=cost, grader=offline_grader, k=k,
                ),
                memory_on=False,
            )
        )
        _ = (mem_default, limit, judge)  # accepted for symmetry
        return records

    @staticmethod
    def _ordered(tasks: Sequence[Task]) -> list[Task]:
        """Stable sort by (group_id, order, original index) — two-phase order."""
        indexed = list(enumerate(tasks))
        return [
            t for _, t in sorted(
                indexed,
                key=lambda it: (
                    it[1].group_id or "",
                    it[1].order,
                    it[0],
                ),
            )
        ]

    @staticmethod
    def _tag(records: list[PerTaskRecord], *, memory_on: bool) -> list[PerTaskRecord]:
        for r in records:
            r.memory_on = memory_on
            r.trajectory.memory_on = memory_on
        return records

    # ------------------------------------------------------------------ #
    # score() — pure, deterministic native metrics + component slices
    # ------------------------------------------------------------------ #
    def score(
        self,
        records: Sequence[PerTaskRecord],
        tasks: Sequence[Task],
        *,
        ks: Sequence[int] = _DEFAULT_KS,
    ) -> BenchmarkNativeReport:
        """Fold records + tasks into the SWE-ContextBench native report."""
        by_id = {t.task_id: t for t in tasks}
        on = [r for r in records if r.memory_on]
        off = [r for r in records if not r.memory_on]

        # Gold sibling ids per related task: explicit gold_memory_ids, else the
        # ids of any earlier sibling (order < this) in the SAME group_id. The
        # latter is the offline stand-in for the Relationship link (the loader
        # leaves gold_memory_ids empty when the dataset ships no context_ids).
        gold_ids = self._gold_sibling_ids(tasks)

        n_tasks = len({r.task_id for r in records})
        rep = self.empty_report(
            "native", n_tasks,
            paper="arXiv 2602.08316",
            dataset="jiayuanz3/SWEContextBench",
            note="resolve_rate offline stand-in = overlap vs gold patch; "
                 "Docker grader optional and skippable.",
        )

        # ---- headline: overall resolve rate (memory-ON pass) -------------- #
        rep.add_metric(self._resolve_metric("resolve_rate", on))

        # ---- core finding: context lift on the RELATED subset ------------- #
        related_on = [r for r in on if self._order(by_id, r) > 0]
        related_off = [r for r in off if self._order(by_id, r) > 0]
        rr_on = self._resolve_rate(related_on)
        rr_off = self._resolve_rate(related_off)
        rep.add_metric(NativeMetric(
            "context_lift", rr_on - rr_off,
            n=len(related_on), better="higher",
            metadata={
                "resolve_rate_context_on": rr_on,
                "resolve_rate_no_context": rr_off,
                "subset": "related (order>0)",
            },
        ))

        # ---- retrieval quality headline: overall matched % ---------------- #
        rep.add_metric(self._overall_matched(related_on, gold_ids, ks))

        # ---- efficiency headline (LOWER better) --------------------------- #
        rep.add_metric(NativeMetric(
            "efficiency", self._efficiency_ratio(on),
            n=len(on), better="lower",
            metadata={"definition": "mean(memory_tokens / total_tokens)"},
        ))
        rep.add_metric(NativeMetric(
            "avg_tokens", mean([r.trajectory.total_tokens for r in on]),
            n=len(on), better="lower",
        ))
        rep.add_metric(NativeMetric(
            "avg_tool_calls", mean([self._tool_calls(r.trajectory) for r in on]),
            n=len(on), better="lower",
        ))

        # ---- components --------------------------------------------------- #
        self._add_subset_components(rep, on, off, by_id)
        self._add_config_components(rep, records, by_id, gold_ids, ks)
        self._add_language_component(rep, on, by_id)
        self._add_difficulty_component(rep, on, by_id)
        self._add_retrieval_quality(rep, related_on, gold_ids, ks)
        self._add_localization(rep, on, by_id)

        rep.metadata["n_experience"] = sum(
            1 for r in on if self._order(by_id, r) == 0
        )
        rep.metadata["n_related"] = len(related_on)
        return rep

    # ------------------------------------------------------------------ #
    # Components
    # ------------------------------------------------------------------ #
    def _add_subset_components(
        self,
        rep: BenchmarkNativeReport,
        on: Sequence[PerTaskRecord],
        off: Sequence[PerTaskRecord],
        by_id: dict[str, Task],
    ) -> None:
        exp_on = [r for r in on if self._order(by_id, r) == 0]
        exp_off = [r for r in off if self._order(by_id, r) == 0]
        rel_on = [r for r in on if self._order(by_id, r) > 0]
        rel_off = [r for r in off if self._order(by_id, r) > 0]

        overall = ComponentScore("overall", n=len(on))
        overall.add(self._resolve_metric("resolve_rate", on))
        rep.add_component(overall)

        exp = ComponentScore("experience_tasks", n=len(exp_on),
                             metadata={"order": "==0", "desc": "base difficulty"})
        exp.add(self._resolve_metric("resolve_rate", exp_on))
        exp.add(NativeMetric(
            "resolve_rate_memory_off", self._resolve_rate(exp_off),
            n=len(exp_off), better="higher",
        ))
        rep.add_component(exp)

        rel = ComponentScore("related_tasks", n=len(rel_on),
                             metadata={"order": ">0", "desc": "context-learning subset"})
        rr_on = self._resolve_rate(rel_on)
        rr_off = self._resolve_rate(rel_off)
        rel.add(NativeMetric("resolve_rate", rr_on, n=len(rel_on), better="higher"))
        rel.add(NativeMetric("resolve_rate_memory_off", rr_off,
                            n=len(rel_off), better="higher"))
        rel.add(NativeMetric("context_lift", rr_on - rr_off,
                            n=len(rel_on), better="higher"))
        rep.add_component(rel)

    def _add_config_components(
        self,
        rep: BenchmarkNativeReport,
        records: Sequence[PerTaskRecord],
        by_id: dict[str, Task],
        gold_ids: dict[str, set[str]],
        ks: Sequence[int],
    ) -> None:
        """One component per paper config (free/oracle × context/summary + baseline).

        Offline, the same A/B passes back every config: the ``no_context`` config
        is scored from the memory-OFF pass; the four memory-ON configs are scored
        from the memory-ON pass. ``oracle`` configs additionally restrict the
        scored gold set to the directly-linked sibling (here: the nearest prior
        sibling), while ``free`` configs score against all siblings — so the
        component carries the resolve rate AND a per-config matched%.
        """
        on = [r for r in records if r.memory_on]
        off = [r for r in records if not r.memory_on]
        related_on = [r for r in on if self._order(by_id, r) > 0]
        for name, cfg in _CONFIGS.items():
            if not cfg["memory"]:
                pool = [r for r in off if self._order(by_id, r) > 0]
                comp = ComponentScore(name, n=len(pool), metadata=dict(cfg))
                comp.add(NativeMetric("resolve_rate", self._resolve_rate(pool),
                                      n=len(pool), better="higher"))
                rep.add_component(comp)
                continue
            comp = ComponentScore(name, n=len(related_on), metadata=dict(cfg))
            comp.add(NativeMetric("resolve_rate", self._resolve_rate(related_on),
                                  n=len(related_on), better="higher"))
            # Oracle restricts gold to the single nearest sibling; free uses all.
            cfg_gold = (
                self._nearest_sibling(by_id, gold_ids) if cfg["oracle"] else gold_ids
            )
            comp.add(self._overall_matched(related_on, cfg_gold, ks))
            rep.add_component(comp)

    def _add_language_component(
        self,
        rep: BenchmarkNativeReport,
        on: Sequence[PerTaskRecord],
        by_id: dict[str, Task],
    ) -> None:
        buckets: dict[str, list[PerTaskRecord]] = {}
        for r in on:
            t = by_id.get(r.task_id)
            lang = (t.competency if t and t.competency else "unknown")
            buckets.setdefault(lang, []).append(r)
        comp = ComponentScore("by_language", n=len(on))
        for lang, recs in sorted(buckets.items()):
            comp.add(NativeMetric(
                f"resolve_rate:{lang}", self._resolve_rate(recs),
                n=len(recs), better="higher",
            ))
        rep.add_component(comp)

    def _add_difficulty_component(
        self,
        rep: BenchmarkNativeReport,
        on: Sequence[PerTaskRecord],
        by_id: dict[str, Task],
    ) -> None:
        buckets: dict[str, list[PerTaskRecord]] = {}
        any_diff = False
        for r in on:
            t = by_id.get(r.task_id)
            diff = str((t.metadata.get("difficulty") if t else None) or "unknown")
            if diff != "unknown":
                any_diff = True
            buckets.setdefault(diff, []).append(r)
        comp = ComponentScore("by_difficulty", n=len(on),
                             metadata={"present": any_diff})
        for diff, recs in sorted(buckets.items()):
            comp.add(NativeMetric(
                f"resolve_rate:{diff}", self._resolve_rate(recs),
                n=len(recs), better="higher",
            ))
        rep.add_component(comp)

    def _add_retrieval_quality(
        self,
        rep: BenchmarkNativeReport,
        related_on: Sequence[PerTaskRecord],
        gold_ids: dict[str, set[str]],
        ks: Sequence[int],
    ) -> None:
        """retrieval_quality@k: match_rate@k + context_recall@k for k in {1,2,3}."""
        comp = ComponentScore("retrieval_quality@k", n=len(related_on))
        for k in ks:
            comp.add(NativeMetric(
                f"match_rate@{k}", self._match_rate(related_on, gold_ids, k),
                n=len(related_on), better="higher",
            ))
            comp.add(NativeMetric(
                f"context_recall@{k}", self._context_recall(related_on, gold_ids, k),
                n=len(related_on), better="higher",
            ))
        comp.add(self._overall_matched(related_on, gold_ids, ks))
        rep.add_component(comp)

    def _add_localization(
        self,
        rep: BenchmarkNativeReport,
        on: Sequence[PerTaskRecord],
        by_id: dict[str, Task],
    ) -> None:
        """Localization at file / function / line granularity (diff-overlap).

        Only tasks whose prediction parses as a unified diff contribute (n
        reflects that), so the offline echo path — where the prediction is not a
        diff — yields n=0 and a 0.0 value WITHOUT crashing.
        """
        file_hits: list[float] = []
        func_hits: list[float] = []
        line_hits: list[float] = []
        for r in on:
            t = by_id.get(r.task_id)
            if t is None or not t.patch:
                continue
            pred_loc = _diff_locations(r.prediction)
            if not pred_loc["files"]:
                continue  # prediction is not a diff -> not localizable
            gold_loc = _diff_locations(t.patch)
            file_hits.append(_superset(pred_loc["files"], gold_loc["files"]))
            func_hits.append(_superset(pred_loc["funcs"], gold_loc["funcs"]))
            line_hits.append(_line_overlap(pred_loc["lines"], gold_loc["lines"]))
        comp = ComponentScore("localization_granularity", n=len(file_hits))
        comp.add(NativeMetric("file_correct_location", mean(file_hits),
                            n=len(file_hits), better="higher"))
        comp.add(NativeMetric("function_correct_location", mean(func_hits),
                            n=len(func_hits), better="higher"))
        comp.add(NativeMetric("line_correct_location", mean(line_hits),
                            n=len(line_hits), better="higher"))
        rep.add_component(comp)

    # ------------------------------------------------------------------ #
    # Metric primitives
    # ------------------------------------------------------------------ #
    def _resolve_metric(self, name: str, recs: Sequence[PerTaskRecord]) -> NativeMetric:
        return NativeMetric(name, self._resolve_rate(recs), n=len(recs), better="higher")

    @staticmethod
    def _resolve_rate(recs: Sequence[PerTaskRecord]) -> float:
        """Fraction resolved. ``success is True`` counts; None/False do not.

        ``success`` is set by the grader during run (overlap stand-in offline,
        the SWE-bench rule under the real Docker grader). An ungraded task
        (``None``, e.g. Docker unavailable) is treated as not-resolved for the
        rate but does not crash — callers can read ``n`` to gauge coverage.
        """
        if not recs:
            return 0.0
        return mean([1.0 if r.success is True else 0.0 for r in recs])

    def _match_rate(
        self,
        recs: Sequence[PerTaskRecord],
        gold_ids: dict[str, set[str]],
        k: int,
    ) -> float:
        """Fraction of tasks whose top-k retrieved ids include ANY gold sibling."""
        if not recs:
            return 0.0
        hits = []
        for r in recs:
            gold = gold_ids.get(r.task_id, set())
            topk = set(_topk_retrieved_ids(r.trajectory, k))
            hits.append(1.0 if (gold and topk & gold) else 0.0)
        return mean(hits)

    def _context_recall(
        self,
        recs: Sequence[PerTaskRecord],
        gold_ids: dict[str, set[str]],
        k: int,
    ) -> float:
        """Mean recall@k of the gold sibling context ids into the top-k set."""
        if not recs:
            return 0.0
        recalls = []
        for r in recs:
            gold = gold_ids.get(r.task_id, set())
            topk = set(_topk_retrieved_ids(r.trajectory, k))
            recalls.append(set_recall(topk, gold))
        return mean(recalls)

    def _overall_matched(
        self,
        recs: Sequence[PerTaskRecord],
        gold_ids: dict[str, set[str]],
        ks: Sequence[int],
    ) -> NativeMetric:
        """'Overall Matched %' — mean of match_rate@k across the reported ks."""
        per_k = {k: self._match_rate(recs, gold_ids, k) for k in ks}
        val = mean(list(per_k.values()))
        return NativeMetric(
            "overall_matched", val, n=len(recs), better="higher",
            metadata={f"match_rate@{k}": per_k[k] for k in ks},
        )

    @staticmethod
    def _efficiency_ratio(recs: Sequence[PerTaskRecord]) -> float:
        """mean(memory_tokens / total_tokens) per task (LOWER better)."""
        ratios = []
        for r in recs:
            total = r.trajectory.total_tokens
            if total > 0:
                ratios.append(r.trajectory.memory_tokens / total)
        return mean(ratios)

    @staticmethod
    def _tool_calls(traj: Trajectory) -> int:
        """Tool-call proxy: count of non-generate/retrieve recorded steps."""
        return sum(1 for s in traj.steps if s.kind in ("write", "note", "error"))

    # ------------------------------------------------------------------ #
    # Gold-sibling derivation
    # ------------------------------------------------------------------ #
    def _gold_sibling_ids(self, tasks: Sequence[Task]) -> dict[str, set[str]]:
        """task_id -> set of gold sibling context ids to be retrieved.

        Priority: the task's explicit ``gold_memory_ids`` (the loader fills these
        from the Relationship/context_ids when the dataset provides them). When
        empty (the common fixture case), fall back to the session/write-back ids
        of EARLIER siblings (order < this) in the SAME ``group_id`` — the offline
        stand-in for the Relationship link. The write-back id the EchoAgent
        produces is ``"<sibling_task_id>::mem0"``; the seeded session ids are the
        sibling's ``Session.session_id``. We include BOTH so match_rate fires on
        whichever the retriever surfaces.
        """
        by_group: dict[str, list[Task]] = {}
        for t in tasks:
            by_group.setdefault(t.group_id or t.task_id, []).append(t)
        for lst in by_group.values():
            lst.sort(key=lambda t: t.order)

        out: dict[str, set[str]] = {}
        for t in tasks:
            if t.gold_memory_ids:
                out[t.task_id] = set(t.gold_memory_ids)
                continue
            gold: set[str] = set()
            for sib in by_group.get(t.group_id or t.task_id, []):
                if sib.task_id == t.task_id or sib.order >= t.order:
                    continue
                gold.add(f"{sib.task_id}::mem0")  # EchoAgent write-back id
                for sess in sib.sessions:
                    gold.add(sess.session_id)
            out[t.task_id] = gold
        return out

    def _nearest_sibling(
        self, by_id: dict[str, Task], gold_ids: dict[str, set[str]]
    ) -> dict[str, set[str]]:
        """Oracle gold: restrict each task's gold to its single nearest prior sibling.

        For the offline fallback (no explicit gold_memory_ids) the nearest prior
        sibling's ids are already the only ones in ``gold_ids`` for a 2-task
        group; for larger groups we keep the highest-order prior sibling's ids.
        When explicit gold ids exist we keep them as-is (the dataset's oracle).
        """
        out: dict[str, set[str]] = {}
        # group tasks by group for nearest lookup
        by_group: dict[str, list[Task]] = {}
        for t in by_id.values():
            by_group.setdefault(t.group_id or t.task_id, []).append(t)
        for lst in by_group.values():
            lst.sort(key=lambda t: t.order)
        for tid, gold in gold_ids.items():
            t = by_id.get(tid)
            if t is None or t.gold_memory_ids:
                out[tid] = set(gold)
                continue
            siblings = [
                s for s in by_group.get(t.group_id or t.task_id, [])
                if s.order < t.order
            ]
            if not siblings:
                out[tid] = set()
                continue
            nearest = max(siblings, key=lambda s: s.order)
            ids = {f"{nearest.task_id}::mem0"}
            ids.update(s.session_id for s in nearest.sessions)
            out[tid] = ids
        return out

    @staticmethod
    def _order(by_id: dict[str, Task], r: PerTaskRecord) -> int:
        t = by_id.get(r.task_id)
        return t.order if t is not None else 0


# --------------------------------------------------------------------------- #
# Module-level helpers (pure, stdlib)
# --------------------------------------------------------------------------- #
def _topk_retrieved_ids(traj: Trajectory, k: int) -> list[str]:
    """Top-k retrieved item ids from the trajectory's FINAL retrieve step.

    The paper's match% is over the retrieved top-k for the current task, so we
    read the last retrieve step (the declared retrieval for the related task),
    truncated to ``k`` and in ranked order.
    """
    steps = [s for s in traj.steps if s.kind == "retrieve"]
    if not steps:
        return []
    ranked = sorted(steps[-1].retrieved, key=lambda ri: ri.rank)
    return [ri.item_id for ri in ranked[: max(0, k)]]


_DIFF_FILE_RE = re.compile(r"^\+\+\+\s+(?:b/)?(\S+)", re.MULTILINE)
_DIFF_FILE_A_RE = re.compile(r"^---\s+(?:a/)?(\S+)", re.MULTILINE)
_GIT_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)
#: ``@@ -l,s +l,s @@ optional-context (often the enclosing function)``.
_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$", re.MULTILINE)


def _diff_locations(diff: str) -> dict[str, set]:
    """Parse a unified diff into edited {files}, {(file,func)}, {(file,line)}.

    Pure stdlib. Returns empty sets when ``diff`` is not a unified diff (the
    offline echo prediction), so callers can detect "not localizable".
    """
    files: set[str] = set()
    funcs: set[tuple[str, str]] = set()
    lines: set[tuple[str, int]] = set()
    if not diff:
        return {"files": files, "funcs": funcs, "lines": lines}

    # File set from git headers + +++/--- markers.
    for m in _GIT_HEADER_RE.finditer(diff):
        files.add(m.group(2))
    for m in _DIFF_FILE_RE.finditer(diff):
        if m.group(1) not in ("/dev/null",):
            files.add(m.group(1))
    if not files:
        for m in _DIFF_FILE_A_RE.finditer(diff):
            if m.group(1) not in ("/dev/null",):
                files.add(m.group(1))

    # Associate each hunk with the most recently named target file.
    current_file = None
    for line in diff.splitlines():
        gm = _GIT_HEADER_RE.match(line)
        if gm:
            current_file = gm.group(2)
            continue
        fm = re.match(r"^\+\+\+\s+(?:b/)?(\S+)", line)
        if fm and fm.group(1) != "/dev/null":
            current_file = fm.group(1)
            continue
        hm = _HUNK_RE.match(line)
        if hm and current_file:
            start = int(hm.group(1))
            span = int(hm.group(2)) if hm.group(2) else 1
            ctx = (hm.group(3) or "").strip()
            if ctx:
                func = _func_name(ctx)
                if func:
                    funcs.add((current_file, func))
            for ln in range(start, start + max(1, span)):
                lines.add((current_file, ln))
    return {"files": files, "funcs": funcs, "lines": lines}


_FUNC_NAME_RE = re.compile(r"(?:def|func|function|fn|class)\s+([A-Za-z_]\w*)")


def _func_name(hunk_context: str) -> Optional[str]:
    """Extract an enclosing function/class name from a hunk's ``@@`` context."""
    m = _FUNC_NAME_RE.search(hunk_context)
    if m:
        return m.group(1)
    # Fall back to the first identifier-looking token.
    m2 = re.search(r"([A-Za-z_]\w+)\s*\(", hunk_context)
    return m2.group(1) if m2 else None


def _superset(pred: set, gold: set) -> float:
    """1.0 if ``pred`` covers every element of ``gold`` (and gold non-empty)."""
    if not gold:
        return 0.0
    return 1.0 if gold <= pred else 0.0


def _line_overlap(pred: set, gold: set) -> float:
    """Jaccard-style line localization: |pred ∩ gold| / |gold| (recall)."""
    if not gold:
        return 0.0
    return len(pred & gold) / len(gold)


__all__ = ["SWEContextBenchNativeEvaluator"]
