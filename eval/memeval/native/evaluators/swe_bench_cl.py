"""SWE-Bench-CL native evaluator (continual-learning CODE benchmark).

Implements the continual-learning evaluation protocol of **SWE-Bench-CL**
(arXiv 2507.00014; dataset ``thomasjoshi/agents-never-forget``,
``data/SWE-Bench-CL-Curriculum.json``), which reorganizes SWE-bench Verified
into 8 per-repo *sequences* (273 tasks) and scores how an agent learns, retains,
and transfers across the strictly-ordered tasks of each sequence.

Faithfulness to the paper / reference code
-----------------------------------------
Metrics mirror the authors' reference implementation
(``eval_v1/eval_procedure.py``: ``calculate_accuracy`` / ``calculate_forgetting``
/ ``calculate_backward_transfer`` / ``calculate_forward_transfer`` /
``calculate_aulc`` / ``calculate_cl_score`` with ``CL_SCORE_WEIGHTS`` all 1.0),
verified against that source rather than from memory:

* **ACC** = ``(1/N) Σ_j a(N,j)`` — per-sequence resolve rate, divided by ``N``.
* **F**   = ``(1/(N-1)) Σ_{j<N} [initial_pass(j) - final_state(j)]`` (``N-1``).
* **BWT** = ``(1/(N-1)) Σ_{i<N} [final_state(i) - initial_pass(i)]`` = ``-F``.
* **FWT** = ``(1/(N-1)) Σ_{i<N} [s_memOn(t_{i+1}) - s_memOff(t_{i+1})]`` (``N-1``).
* **AULC** = ``(1/N) Σ_{i=1..N} (1/i) Σ_{k<=i} initial_pass(k)`` (``N``).
* **CL-Score** = ``ACC - F + FWT + BWT + AULC`` (all λ = 1.0).

The card-defined stability/plasticity trio (NOT in ``eval_procedure.py`` but in
the dataset's ``evaluation_metrics``) is derived from the same vectors:

* **CL-Plasticity** = mean of the ``initial_pass`` diagonal ``a(i,i)`` = CL-P.
* **CL-Stability**  = ``1 - F`` = CL-S.
* **CL-F1**         = ``2·CL-P·CL-S / (CL-P + CL-S)`` (β = 1; general CL-Fβ).

Three result vectors per sequence (per the spec)
-----------------------------------------------
* ``initial_pass`` (memory-ON, first solve in order) — the diagonal ``a(i,i)``.
* ``final_state``  (memory-ON, end-of-sequence re-test) — ``a(N,j)``. When the
  re-test pass is not run the code FALLS BACK to ``final_state = initial_pass``
  (so F = BWT = 0), exactly as the reference ``calculate_forgetting`` does.
* ``per-task memory-OFF`` — the zero-shot baseline ``a-bar(0,·)``; supplies the
  mem-off side of Forward Transfer.

Offline safety
--------------
``score`` is pure and deterministic. ``run`` drives the EXACT loaded task list
through the reused :meth:`BaseNativeEvaluator.run_tasks` (EchoAgent + per-group
InMemoryStore), strictly ordered by ``Task.order`` within each ``Task.group_id``,
and reset between groups (the harness store policy is per-group). The CODE
resolve grader (:class:`memeval.grader.LocalExecGrader`, run in a local venv)
**must never hard-fail offline**: we resolve the grader via
:func:`memeval.grader.get_grader` only when the caller asks for it, default to
the dependency-free ``overlap`` stand-in, and fall back to ``Trajectory.success``
if grading yields ``None`` — so the offline path needs no network and no heavy
deps.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ...schema import Benchmark, Task, Trajectory
from ..judge import Judge
from ..spec import (
    BenchmarkNativeReport,
    ComponentScore,
    NativeMetric,
    PerTaskRecord,
)
from .base import BaseNativeEvaluator, mean, mode_to_memory

#: Reference CL-Score weights (eval_procedure.CL_SCORE_WEIGHTS) — all 1.0.
_CL_WEIGHTS = {
    "lambda_F": 1.0,
    "lambda_FT": 1.0,
    "lambda_BWT": 1.0,
    "lambda_AULC": 1.0,
}

#: Phase tag stored on each PerTaskRecord.extra so score() can split the three
#: result vectors back out (initial-pass diagonal, final-state re-test, mem-off).
_PHASE_INITIAL = "initial_pass"
_PHASE_FINAL = "final_state"
_PHASE_MEMOFF = "mem_off"

#: Trajectory step kinds that count as agent "actions" for tool-use efficiency.
#: The harness records actions (retrieve / generate / write), not a sub-agent's
#: individual tool calls, so TUE is a faithful proxy over what is recorded.
_ACTION_KINDS = frozenset({"retrieve", "generate", "write"})


class SWEBenchCLNativeEvaluator(BaseNativeEvaluator):
    """Native evaluator for SWE-Bench-CL (continual-learning, code)."""

    benchmark: str = Benchmark.SWE_BENCH_CL.value

    # ------------------------------------------------------------------ #
    # run: three result vectors per sequence (initial / final / mem-off)
    # ------------------------------------------------------------------ #
    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        mode: str = "off",
        store: Any = None,
        judge: Optional[Judge] = None,  # unused: SWE-Bench-CL is test-execution, no LLM judge
        cost: Any = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """Drive the continual-learning passes and return tagged records.

        Produces, per sequence (``group_id``), in strict ``order``:

        * a memory-ON pass — its successes are the ``initial_pass`` diagonal
          ``a(i,i)``;
        * a memory-ON end-of-sequence RE-TEST pass — its successes are the
          ``final_state`` row ``a(N,j)`` (enabled by ``retest=True``, default;
          when disabled, ``score`` falls back to ``final_state = initial_pass``);
        * a memory-OFF pass — the zero-shot baseline supplying Forward Transfer.

        Each record is tagged in ``extra['phase']`` so ``score`` can recover the
        three vectors. Grading is via the reused offline grader (default
        ``overlap``), falling back to ``Trajectory.success``; the local-execution
        resolve grader is used only when ``grader='local'`` is passed AND degrades
        gracefully (grading ``None`` -> trajectory success).
        """
        k = int(kwargs.get("k", 5))
        retest = bool(kwargs.get("retest", True))
        grader = self._resolve_grader(kwargs.get("grader"))

        ordered = self._ordered(tasks)

        records: list[PerTaskRecord] = []

        # (1) memory-ON pass -> initial-pass diagonal a(i,i).
        on_recs = self.run_tasks(
            ordered, agent_or_model=agent_or_model, memory=True,
            store=store, cost=cost, grader=grader, k=k,
        )
        for r in on_recs:
            r.extra["phase"] = _PHASE_INITIAL
            records.append(r)

        # (2) memory-ON end-of-sequence RE-TEST -> final-state row a(N,j).
        # Re-driving the SAME ordered tasks once more after the whole sequence
        # has been seen; in real (learning) runs this can differ from the
        # initial pass (forgetting / backward transfer). Offline EchoAgent is
        # deterministic, so final == initial and F = BWT = 0 (correct).
        if retest:
            final_recs = self.run_tasks(
                ordered, agent_or_model=agent_or_model, memory=True,
                store=store, cost=cost, grader=grader, k=k,
            )
            for r in final_recs:
                r.extra["phase"] = _PHASE_FINAL
                records.append(r)

        # (3) memory-OFF pass -> zero-shot baseline a-bar(0,.) for Forward Transfer.
        off_recs = self.run_tasks(
            ordered, agent_or_model=agent_or_model, memory=False,
            store=None, cost=cost, grader=grader, k=k,
        )
        for r in off_recs:
            r.extra["phase"] = _PHASE_MEMOFF
            records.append(r)

        return records

    # ------------------------------------------------------------------ #
    # score: pure / deterministic CL metric folding
    # ------------------------------------------------------------------ #
    def score(
        self,
        records: Sequence[PerTaskRecord],
        tasks: Sequence[Task],
    ) -> BenchmarkNativeReport:
        """Fold the three vectors into the paper-native CL report.

        Pure: no model, no network, no wall-clock. Computes ACC / F / BWT / FWT
        / AULC / CL-Score and the CL-P / CL-S / CL-F1 trio per sequence, then
        macro-averages across sequences for the headline numbers. Each sequence
        is a component; difficulty strata and the memory-condition contrast are
        additional components.
        """
        by_id = {t.task_id: t for t in tasks}
        mode = self._infer_mode(records)
        report = self.empty_report(
            mode,
            len({r.task_id for r in records}),
            paper="arXiv:2507.00014",
            dataset="thomasjoshi/agents-never-forget",
            cl_weights=dict(_CL_WEIGHTS),
        )

        # phase -> {task_id: bool}
        phases = self._phase_maps(records)
        initial = phases[_PHASE_INITIAL]
        final = phases[_PHASE_FINAL] or dict(initial)  # fallback: final == initial
        memoff = phases[_PHASE_MEMOFF]
        retested = bool(phases[_PHASE_FINAL])

        # group_id -> ordered list of task_ids (strict Task.order).
        groups = self._group_order(tasks)

        per_seq: dict[str, dict[str, float]] = {}
        for gid, order in groups.items():
            # Only score tasks we actually have an initial-pass result for.
            seq_order = [tid for tid in order if tid in initial]
            if not seq_order:
                continue
            metrics = self._sequence_metrics(seq_order, initial, final, memoff)
            per_seq[gid] = metrics

            comp = ComponentScore(
                name=gid, n=len(seq_order),
                metadata={"repo": self._repo_of(seq_order, by_id), "retested": retested},
            )
            for mname, better in _SEQ_METRIC_DIRECTIONS.items():
                comp.add(NativeMetric(mname, metrics[mname], n=len(seq_order), better=better))
            report.add_component(comp)

        # -- headline = macro-average across sequences (paper's per-sequence-then-mean) #
        if per_seq:
            for mname, better in _SEQ_METRIC_DIRECTIONS.items():
                report.add_metric(
                    NativeMetric(
                        mname,
                        mean([s[mname] for s in per_seq.values()]),
                        n=len(per_seq),
                        better=better,
                        metadata={"aggregation": "macro_over_sequences"},
                    )
                )

        # -- difficulty-stratum components (Task.metadata.difficulty 1..4) ---- #
        self._add_difficulty_components(report, initial, final, by_id)

        # -- memory-condition contrast component (mem-on vs mem-off accuracy) -- #
        self._add_memory_condition_component(report, final, initial, memoff)

        # -- snapshot-phase component (initial-pass vs final-state means) ----- #
        self._add_snapshot_component(report, initial, final, retested)

        # -- Tool-Use Efficiency: resolutions per recorded agent action step -- #
        # Computed from the memory-ON initial pass: resolved tasks / total recorded
        # agent action steps (retrieve/generate/write) across those trajectories --
        # a proxy for how efficiently the agent spends actions to resolve tasks
        # (higher = fewer actions per resolution). The harness records actions, not
        # a sub-agent's individual tool calls, so this is a faithful proxy over what
        # is recorded, not a literal tool-call count. The reference eval_procedure.py
        # left this N/A; we compute it when there is signal, and report it honestly
        # UNCOMPUTABLE (n=0) when no action steps were recorded -- never a fake 0.0.
        init_recs = [r for r in records if r.extra.get("phase") == _PHASE_INITIAL]
        action_steps = sum(
            1 for r in init_recs for s in r.trajectory.steps if s.kind in _ACTION_KINDS
        )
        resolved_ct = sum(1 for r in init_recs if r.success)
        if action_steps > 0:
            report.add_metric(NativeMetric(
                "tool_use_efficiency", resolved_ct / action_steps,
                n=len(init_recs), better="higher",
                metadata={
                    "formula": "resolved_initial / action_steps_initial",
                    "action_kinds": sorted(_ACTION_KINDS),
                    "resolved": resolved_ct, "action_steps": action_steps,
                },
            ))
        else:
            report.add_metric(NativeMetric(
                "tool_use_efficiency", 0.0, n=0, better="higher",
                metadata={"status": "uncomputable",
                          "note": "no agent action steps recorded in trajectories"},
            ))

        report.metadata["n_sequences"] = len(per_seq)
        report.metadata["retested"] = retested
        return report

    # ------------------------------------------------------------------ #
    # per-sequence CL metric computation (the paper's formulas)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _sequence_metrics(
        order: list[str],
        initial: dict[str, bool],
        final: dict[str, bool],
        memoff: dict[str, bool],
    ) -> dict[str, float]:
        """All per-sequence CL metrics over one ordered sequence of task ids."""
        N = len(order)

        def ip(tid: str) -> int:
            return 1 if initial.get(tid, False) else 0

        def fs(tid: str) -> int:
            return 1 if final.get(tid, False) else 0

        def mo(tid: str) -> int:
            return 1 if memoff.get(tid, False) else 0

        # ACC = (1/N) sum_j a(N,j); a(N,j) is the final-state (end-of-sequence) row.
        acc = mean([float(fs(tid)) for tid in order]) if N else 0.0

        # AULC = (1/N) sum_{i=1..N} (1/i) sum_{k<=i} initial_pass(k).
        if N:
            aulc = sum((1.0 / i) * sum(ip(order[kk]) for kk in range(i)) for i in range(1, N + 1)) / N
        else:
            aulc = 0.0

        # CL-Plasticity = mean diagonal a(i,i) over ALL N tasks.
        cl_p = mean([float(ip(tid)) for tid in order]) if N else 0.0

        # F / BWT / FWT use N-1 transitions over the first N-1 tasks (per ref code).
        #   F   = (1/(N-1)) sum_{j<N-1} [initial_pass(j) - final_state(j)]
        #   BWT = (1/(N-1)) sum_{i<N-1} [final_state(i) - initial_pass(i)] = -F
        #   FWT = (1/(N-1)) sum_{i<N-1} [s_memOn(t_{i+1}) - s_memOff(t_{i+1})]
        # The mem-ON per-task vector IS the initial-pass diagonal (reference
        # ``task_results_mem_enabled``); the mem-OFF vector is the zero-shot
        # baseline (``task_results_no_mem``).
        if N > 1:
            f = sum(ip(order[j]) - fs(order[j]) for j in range(N - 1)) / (N - 1)
            bwt = sum(fs(order[i]) - ip(order[i]) for i in range(N - 1)) / (N - 1)
            ft = sum(ip(order[i + 1]) - mo(order[i + 1]) for i in range(N - 1)) / (N - 1)
        else:
            f = bwt = ft = 0.0

        # CL-Stability = 1 - F; CL-F1 = harmonic mean of plasticity & stability.
        cl_s = 1.0 - f
        denom = cl_p + cl_s
        cl_f1 = (2.0 * cl_p * cl_s / denom) if denom > 0.0 else 0.0

        # Composite CL-Score (additive, all lambdas 1.0).
        cl_score = (
            acc
            - _CL_WEIGHTS["lambda_F"] * f
            + _CL_WEIGHTS["lambda_FT"] * ft
            + _CL_WEIGHTS["lambda_BWT"] * bwt
            + _CL_WEIGHTS["lambda_AULC"] * aulc
        )

        return {
            "accuracy": acc,
            "forgetting": f,
            "backward_transfer": bwt,
            "forward_transfer": ft,
            "aulc": aulc,
            "cl_plasticity": cl_p,
            "cl_stability": cl_s,
            "cl_f1": cl_f1,
            "cl_score": cl_score,
        }

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ordered(tasks: Sequence[Task]) -> list[Task]:
        """Tasks sorted by (group_id, order) — the strict continual order."""
        return sorted(tasks, key=lambda t: (str(t.group_id or ""), int(t.order)))

    @staticmethod
    def _group_order(tasks: Sequence[Task]) -> dict[str, list[str]]:
        """group_id -> task_ids strictly ordered by Task.order."""
        groups: dict[str, list[Task]] = {}
        for t in tasks:
            groups.setdefault(str(t.group_id or "default"), []).append(t)
        out: dict[str, list[str]] = {}
        for gid, ts in groups.items():
            out[gid] = [t.task_id for t in sorted(ts, key=lambda t: int(t.order))]
        return out

    @staticmethod
    def _phase_maps(records: Sequence[PerTaskRecord]) -> dict[str, dict[str, bool]]:
        """Split records by their ``extra['phase']`` into {task_id: bool} maps."""
        maps: dict[str, dict[str, bool]] = {
            _PHASE_INITIAL: {}, _PHASE_FINAL: {}, _PHASE_MEMOFF: {},
        }
        for r in records:
            phase = r.extra.get("phase")
            if phase not in maps:
                # Untagged record: treat by its memory flag (defensive).
                phase = _PHASE_INITIAL if r.memory_on else _PHASE_MEMOFF
            maps[phase][r.task_id] = bool(r.success)
        return maps

    @staticmethod
    def _repo_of(order: list[str], by_id: dict[str, Task]) -> str:
        for tid in order:
            t = by_id.get(tid)
            if t is not None and t.repo:
                return str(t.repo)
        return ""

    def _add_difficulty_components(
        self,
        report: BenchmarkNativeReport,
        initial: dict[str, bool],
        final: dict[str, bool],
        by_id: dict[str, Task],
    ) -> None:
        """Stratify accuracy + plasticity by difficulty_score (1..4)."""
        strata: dict[str, list[str]] = {}
        for tid in initial:
            t = by_id.get(tid)
            diff = None
            if t is not None:
                diff = t.metadata.get("difficulty")
            key = f"difficulty_{diff}" if diff is not None else "difficulty_unknown"
            strata.setdefault(key, []).append(tid)
        for key, tids in sorted(strata.items()):
            comp = ComponentScore(name=key, n=len(tids))
            comp.add(NativeMetric(
                "accuracy",
                mean([1.0 if final.get(tid, initial.get(tid, False)) else 0.0 for tid in tids]),
                n=len(tids), better="higher",
            ))
            comp.add(NativeMetric(
                "cl_plasticity",
                mean([1.0 if initial.get(tid, False) else 0.0 for tid in tids]),
                n=len(tids), better="higher",
            ))
            report.add_component(comp)

    def _add_memory_condition_component(
        self,
        report: BenchmarkNativeReport,
        final: dict[str, bool],
        initial: dict[str, bool],
        memoff: dict[str, bool],
    ) -> None:
        """memory_enabled vs memory_disabled accuracy (drives the lift)."""
        # mem-on accuracy uses final-state (a(N,j)); mem-off is the baseline.
        on_ids = list(final) or list(initial)
        on_src = final if final else initial
        comp = ComponentScore(name="memory_enabled", n=len(on_ids))
        comp.add(NativeMetric(
            "accuracy",
            mean([1.0 if on_src.get(tid, False) else 0.0 for tid in on_ids]),
            n=len(on_ids), better="higher",
        ))
        report.add_component(comp)

        off = ComponentScore(name="memory_disabled", n=len(memoff))
        off.add(NativeMetric(
            "accuracy",
            mean([1.0 if v else 0.0 for v in memoff.values()]),
            n=len(memoff), better="higher",
        ))
        report.add_component(off)

    def _add_snapshot_component(
        self,
        report: BenchmarkNativeReport,
        initial: dict[str, bool],
        final: dict[str, bool],
        retested: bool,
    ) -> None:
        """initial-pass (diagonal a(i,i)) vs final-state (a(N,j)) means."""
        comp = ComponentScore(
            name="snapshot_phase",
            n=len(initial),
            metadata={"retested": retested},
        )
        comp.add(NativeMetric(
            "initial_pass_mean",
            mean([1.0 if v else 0.0 for v in initial.values()]),
            n=len(initial), better="higher",
        ))
        fs_src = final if final else initial
        comp.add(NativeMetric(
            "final_state_mean",
            mean([1.0 if v else 0.0 for v in fs_src.values()]),
            n=len(fs_src), better="higher",
        ))
        report.add_component(comp)

    @staticmethod
    def _infer_mode(records: Sequence[PerTaskRecord]) -> str:
        """Report mode: 'continual' (has the mem-on/off A/B) else 'off'."""
        has_on = any(r.extra.get("phase") in (_PHASE_INITIAL, _PHASE_FINAL) for r in records)
        has_off = any(r.extra.get("phase") == _PHASE_MEMOFF for r in records)
        if has_on and has_off:
            return "continual"
        return "on" if has_on else "off"

    @staticmethod
    def _resolve_grader(grader_spec: Any):
        """Resolve an offline-safe CODE grader.

        ``None`` / falsy -> the dependency-free ``overlap`` stand-in (no real
        test execution). A callable is used as-is. A string is resolved via
        :func:`memeval.grader.get_grader` (valid keys: ``local`` / ``overlap`` /
        ``none``); the local-execution resolve grader (``local`` ->
        :class:`memeval.grader.LocalExecGrader`, run in a local venv) yields
        ``None`` (ungraded) when it cannot evaluate rather than hard-failing —
        ``run_tasks`` then falls back to ``Trajectory.success``.
        """
        from ...grader import get_grader, overlap_grader

        if grader_spec is None or grader_spec == "":
            return lambda task, pred: overlap_grader(task, pred)
        if callable(grader_spec):
            return grader_spec
        return get_grader(str(grader_spec).strip().lower())


#: Per-sequence metric name -> optimization direction. ``forgetting`` is the only
#: lower-is-better member (0 = no forgetting); everything else is higher-better.
_SEQ_METRIC_DIRECTIONS: dict[str, str] = {
    "accuracy": "higher",
    "forgetting": "lower",
    "backward_transfer": "higher",
    "forward_transfer": "higher",
    "aulc": "higher",
    "cl_plasticity": "higher",
    "cl_stability": "higher",
    "cl_f1": "higher",
    "cl_score": "higher",
}


__all__ = ["SWEBenchCLNativeEvaluator"]
