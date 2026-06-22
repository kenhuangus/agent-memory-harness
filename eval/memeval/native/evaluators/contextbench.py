"""ContextBench native evaluator — in-task context *retrieval* quality.

Paper / source
--------------
* arXiv 2602.05892 · GitHub ``EuniAI/ContextBench`` · HF ``Contextbench/ContextBench``.
* 1,136 issue-resolution tasks over 66 repos / 8 languages, each augmented with
  HUMAN-ANNOTATED gold contexts expressed as ``{file, start_line, end_line,
  content}`` spans. ContextBench's headline signal is **how well a system
  retrieves the gold context** — measured as retrieval **recall, precision (and
  their F1) plus efficiency** at three GRANULARITIES (file / block / line) —
  rather than final patch success. (Patch/resolve success is the downstream
  SWE-bench signal; ContextBench isolates the *localization* step.)

What this evaluator computes (faithful to the paper)
----------------------------------------------------
For each task we take the items the agent *declared* it retrieved (the final
retrieve step's set, the ContextBench-style "predicted context"), and compare
them to the task's gold spans at three granularities:

* **file** — set of distinct files. Recall = |pred_files ∩ gold_files| /
  |gold_files|; precision the dual; F1 the harmonic mean.
* **block** — set of gold *spans* identified by ``file:start-end`` (one span ==
  one annotated block). This is the span-level localization the loader keys
  memory on, so recall/precision are over the span ids directly.
* **line** — set of ``(file, line)`` pairs expanded from each span's
  ``[start_line, end_line]`` range (``expand_line_set``). This is the fine-grained
  overlap the paper's line-level recall/precision report.

Headline (overall) metrics — the means across tasks at each granularity:
``file_recall/precision/f1``, ``block_recall/precision/f1``,
``line_recall/precision/f1``, plus ``avg_retrieved`` (mean predicted-set size)
and ``memory_token_overhead`` (mean ``memory_tokens / total_tokens``; LOWER is
better). NOTE: ``memory_token_overhead`` is a harness token-accounting ratio, NOT
the paper's Efficiency metric — see the process-metrics block below for the real
ContextBench Efficiency.

Process / dynamics metrics (Appendix H, Eqs. 4-10) — faithful to the paper
-------------------------------------------------------------------------
ContextBench reports THREE trajectory-level process metrics over the sequence of
intermediate context snapshots ``{C_t^A}_{t=1..T}`` (Eq. 4: ``A^(t) = ∪_{i≤t}
C_i^A``), here read from the agent's per-step retrieve actions at **block**
granularity:

* **efficiency** (AUC-Cov, Eq. 5, HIGHER better) =
  ``(1/T)·Σ_{t=1..T} Recall(A^(t), C^G)`` — normalized area under the cumulative
  gold-coverage curve (how EARLY the agent reaches high coverage). Table 5
  reports Efficiency↑ (e.g. Claude Sonnet 4.5 ≈ 0.658). This REPLACES the former
  mislabeled token-overhead ratio.
* **redundancy** (Eqs. 6-7, LOWER better) =
  ``(1/(T-1))·Σ_{t=2..T} |C_t^A ∩ (∪_{i<t} C_i^A)| / |C_t^A|`` — fraction of each
  new snapshot already seen. Defined only for ``T≥2``.
* **evidence_drop** (Eqs. 8-10, LOWER better) =
  ``1 - |C^A ∩ C^G| / |G_seen|`` where ``G_seen = (∪_t C_t^A) ∩ C^G`` — gold
  evidence observed during exploration but dropped from the final patch context
  ``C^A``. Defined only when ``G_seen`` is non-empty.

Offline honesty: the EchoAgent performs exactly ONE retrieve per task, so
``T == 1``. With a single snapshot, redundancy is undefined (needs ``T≥2``) and
AUC-Cov/Drop collapse to the trivial single-step value; rather than report a
fabricated number, each process metric is computed ONLY over tasks whose
trajectory actually supplies the snapshots it needs, and DEGRADES to ``n=0``
(skipped, value ``0.0``) when none do — exactly the skip-graceful pattern
``resolve_rate`` uses. A real multi-step agent (e.g. the online ClaudeCode agent
logging a retrieve step per file observation) populates them.

Components — one :class:`ComponentScore` per **language** (the loader's
``competency`` stratum), each carrying that slice's file/block/line F1s, plus a
per-granularity component (``granularity:file|block|line``) bundling recall +
precision + F1 so a dashboard can pivot either way.

Resolve rate (optional, grader-gated, never hard-fails offline)
--------------------------------------------------------------
ContextBench is fundamentally a *retrieval* benchmark, but the rows carry SWE-
bench fields (``patch``/``f2p``/``p2p``), so a resolve-rate signal is offered as
a SECONDARY metric when a real grader is available. It is computed from the
per-task ``success`` flags ONLY when present; offline (EchoAgent + no grader)
those are ``None`` and the metric degrades to ``n=0`` (skipped) — it NEVER runs
tests or hard-fails. A real run can pass ``grader=memeval.grader.get_grader(
"local")`` (:class:`memeval.grader.LocalExecGrader`) through ``run`` to populate
it.

Offline: EchoAgent + InMemoryStore + DeterministicJudge, stdlib only, no network,
no real test execution.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ...schema import Benchmark, Session, Task
from ..spec import (
    BenchmarkNativeReport,
    ComponentScore,
    NativeMetric,
    PerTaskRecord,
)
from .base import (
    BaseNativeEvaluator,
    expand_line_set,
    f1,
    mean,
    mode_to_memory,
    retrieved_item_ids,
    set_precision,
    set_recall,
)

#: The granularities ContextBench reports retrieval quality at.
_GRANULARITIES = ("file", "block", "line")


class ContextBenchNativeEvaluator(BaseNativeEvaluator):
    """Native ContextBench evaluator: file/block/line retrieval recall+precision+F1."""

    benchmark: str = Benchmark.CONTEXTBENCH.value

    # ------------------------------------------------------------------ #
    # run — drive the agent once, capture each task's predicted context set
    # ------------------------------------------------------------------ #
    def run(
        self,
        tasks: Sequence[Task],
        *,
        agent_or_model: Any = None,
        mode: str = "off",
        store: Any = None,
        judge: Any = None,  # ContextBench is retrieval-scored; judge unused.
        cost: Any = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> list[PerTaskRecord]:
        """One pass over ``tasks`` → one :class:`PerTaskRecord` per task.

        Reuses :meth:`BaseNativeEvaluator.run_tasks` (EchoAgent + per-group
        InMemoryStore offline) so every trajectory comes back with ranked
        retrieve steps + ``is_gold`` annotated. A ``grader`` may be threaded in
        for the optional resolve-rate signal; offline none is passed, so
        ``success`` stays ``None`` and resolve rate is skipped.
        """
        return self.run_tasks(
            tasks,
            agent_or_model=agent_or_model,
            memory=mode_to_memory(mode),
            store=store,
            cost=cost,
            grader=kwargs.get("grader"),
            k=kwargs.get("k", 5),
        )

    # ------------------------------------------------------------------ #
    # score — pure, deterministic: fold records+tasks into the report
    # ------------------------------------------------------------------ #
    def score(
        self,
        records: Sequence[PerTaskRecord],
        tasks: Sequence[Task],
    ) -> BenchmarkNativeReport:
        by_id = {t.task_id: t for t in tasks}
        mode = records[0].trajectory.memory_on if records else False
        report = self.empty_report(
            "builtin" if mode else "off",
            len(records),
            paper="ContextBench (arXiv:2602.05892, EuniAI/ContextBench)",
            metric_note=(
                "in-task context retrieval recall/precision/F1 at file/block/line "
                "granularity; process metrics efficiency(AUC-Cov)/redundancy/"
                "evidence_drop (Eqs.5-10) over the retrieve-step trajectory, "
                "skipped (n=0) when the trajectory has too few snapshots (offline "
                "single-step); resolve_rate is a grader-gated secondary signal, "
                "skipped offline (n=0). memory_token_overhead is a harness token "
                "ratio, NOT the paper's Efficiency."
            ),
        )

        # Per-task per-granularity recall/precision (means feed the headline +
        # components). Accumulate parallel lists keyed by granularity.
        rec: dict[str, list[float]] = {g: [] for g in _GRANULARITIES}
        prec: dict[str, list[float]] = {g: [] for g in _GRANULARITIES}
        f1s: dict[str, list[float]] = {g: [] for g in _GRANULARITIES}
        overhead: list[float] = []          # memory_token ratio (harness signal)
        retrieved_sizes: list[float] = []
        # Process / dynamics metrics (Eqs. 5-10), each over only the tasks whose
        # trajectory supplies the snapshots it needs (skip-graceful offline).
        auc_cov: list[float] = []           # efficiency (AUC-Cov, Eq. 5)
        redundancy: list[float] = []        # redundancy (Eqs. 6-7)
        evidence_drop: list[float] = []     # evidence drop (Eqs. 8-10)
        # Per-language slices.
        by_lang: dict[str, dict[str, list[float]]] = {}
        # Resolve-rate (secondary): only count tasks the grader actually scored.
        resolved_hits = 0
        resolved_n = 0

        for r in records:
            task = by_id.get(r.task_id)
            if task is None:
                continue
            gold = _gold_sets(task)
            pred = _predicted_sets(r, task)

            lang = (task.competency or "unknown")
            lang_acc = by_lang.setdefault(
                lang, {f"{g}_{m}": [] for g in _GRANULARITIES for m in ("r", "p", "f")}
            )

            for g in _GRANULARITIES:
                gr = set_recall(pred[g], gold[g])
                gp = set_precision(pred[g], gold[g])
                gf = f1(gp, gr)
                rec[g].append(gr)
                prec[g].append(gp)
                f1s[g].append(gf)
                lang_acc[f"{g}_r"].append(gr)
                lang_acc[f"{g}_p"].append(gp)
                lang_acc[f"{g}_f"].append(gf)

            overhead.append(_memory_token_overhead(r))
            retrieved_sizes.append(float(len(pred["block"])))

            # -- process metrics over the per-step retrieve trajectory ------- #
            snapshots = _step_block_snapshots(r)  # [C_1^A, C_2^A, ...] block sets
            gold_blocks = gold["block"]
            final_pred = pred["block"]            # C^A (final/declared context)
            ac = _auc_cov(snapshots, gold_blocks)
            if ac is not None:
                auc_cov.append(ac)
            rd = _redundancy(snapshots)
            if rd is not None:
                redundancy.append(rd)
            dr = _evidence_drop(snapshots, final_pred, gold_blocks)
            if dr is not None:
                evidence_drop.append(dr)

            if r.success is not None:
                resolved_n += 1
                if r.success:
                    resolved_hits += 1

        n = len(records)

        # -- headline metrics: per-granularity recall/precision/F1 --------- #
        for g in _GRANULARITIES:
            report.add_metric(NativeMetric(f"{g}_recall", mean(rec[g]), n=n, better="higher"))
            report.add_metric(NativeMetric(f"{g}_precision", mean(prec[g]), n=n, better="higher"))
            report.add_metric(NativeMetric(f"{g}_f1", mean(f1s[g]), n=n, better="higher"))

        # -- process / dynamics metrics (Eqs. 5-10), skip-graceful --------- #
        report.add_metric(
            NativeMetric(
                "efficiency", mean(auc_cov), n=len(auc_cov), better="higher",
                metadata={
                    "formula": "AUC-Cov = (1/T)*sum_t Recall(A^(t), C^G) "
                               "(Eq.5, block granularity)",
                    "paper_metric": "Efficiency (Table 5, higher is better)",
                    "skipped_when": "trajectory has no retrieve snapshots",
                },
            )
        )
        report.add_metric(
            NativeMetric(
                "redundancy", mean(redundancy), n=len(redundancy), better="lower",
                metadata={
                    "formula": "(1/(T-1))*sum_{t>=2} |C_t & union_{i<t} C_i| / |C_t| "
                               "(Eqs.6-7)",
                    "paper_metric": "Redundancy (Table 5, lower is better)",
                    "skipped_when": "T<2 (needs >=2 retrieve steps; offline T=1)",
                },
            )
        )
        report.add_metric(
            NativeMetric(
                "evidence_drop", mean(evidence_drop), n=len(evidence_drop), better="lower",
                metadata={
                    "formula": "1 - |C^A & C^G| / |(union_t C_t^A) & C^G| (Eqs.8-10)",
                    "paper_metric": "Usage/Evidence Drop (Table 5, lower is better)",
                    "skipped_when": "no gold evidence observed in trajectory (G_seen empty)",
                },
            )
        )

        # memory_token_overhead (harness token ratio, NOT the paper's Efficiency)
        # + avg predicted-context size.
        report.add_metric(
            NativeMetric("memory_token_overhead", mean(overhead), n=n, better="lower",
                         metadata={
                             "formula": "mean(memory_tokens / total_tokens)",
                             "note": "harness token-accounting ratio; NOT "
                                     "ContextBench Efficiency (see 'efficiency').",
                         })
        )
        report.add_metric(
            NativeMetric("avg_retrieved", mean(retrieved_sizes), n=n, better="lower",
                         metadata={"note": "mean predicted-context block count"})
        )
        # resolve_rate: secondary, skip-graceful (n=0 offline -> value 0.0).
        report.add_metric(
            NativeMetric(
                "resolve_rate",
                (resolved_hits / resolved_n) if resolved_n else 0.0,
                n=resolved_n,
                better="higher",
                metadata={
                    "secondary": True,
                    "note": "fraction of graded tasks resolved; skipped (n=0) "
                            "when no CODE grader supplied (offline).",
                },
            )
        )

        # -- per-granularity components (recall+precision+F1 bundled) ------- #
        for g in _GRANULARITIES:
            comp = ComponentScore(name=f"granularity:{g}", n=n)
            comp.add(NativeMetric(f"{g}_recall", mean(rec[g]), n=n, better="higher"))
            comp.add(NativeMetric(f"{g}_precision", mean(prec[g]), n=n, better="higher"))
            comp.add(NativeMetric(f"{g}_f1", mean(f1s[g]), n=n, better="higher"))
            report.add_component(comp)

        # -- per-language components (the paper's stratification) ---------- #
        lang_counts = _count_by_lang(records, by_id)
        for lang in sorted(by_lang):
            acc = by_lang[lang]
            comp = ComponentScore(name=f"language:{lang}", n=lang_counts.get(lang, 0))
            for g in _GRANULARITIES:
                comp.add(NativeMetric(f"{g}_recall", mean(acc[f"{g}_r"]),
                                      n=comp.n, better="higher"))
                comp.add(NativeMetric(f"{g}_precision", mean(acc[f"{g}_p"]),
                                      n=comp.n, better="higher"))
                comp.add(NativeMetric(f"{g}_f1", mean(acc[f"{g}_f"]),
                                      n=comp.n, better="higher"))
            report.add_component(comp)

        return report


# --------------------------------------------------------------------------- #
# Pure helpers (no model, no network) — gold/predicted set extraction
# --------------------------------------------------------------------------- #
def _span_loc(session: Session) -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Pull ``(file, start_line, end_line)`` out of a gold-span Session.

    The loader stuffs these into ``Session.metadata`` (keys ``file`` /
    ``start_line`` / ``end_line``); fall back to parsing the ``file:start-end``
    session id when metadata is absent.
    """
    md = session.metadata or {}
    file = md.get("file")
    start = _as_int(md.get("start_line"))
    end = _as_int(md.get("end_line"))
    if file is None and ":" in session.session_id:
        # id form "path/to/file.py:120-135"
        loc, _, span = session.session_id.rpartition(":")
        if "-" in span and loc:
            lo, _, hi = span.partition("-")
            li, hk = _as_int(lo), _as_int(hi)
            if li is not None and hk is not None:
                file, start, end = loc, li, hk
    return (str(file) if file is not None else None, start, end)


def _gold_sets(task: Task) -> dict[str, set]:
    """The gold {file, block, line} sets for ``task`` from its gold-span sessions.

    block ids are the span session ids (``file:start-end``), which is exactly
    ``task.gold_memory_ids`` by construction; file/line are derived from each
    span's metadata.
    """
    gold_ids = set(task.gold_memory_ids)
    files: set[str] = set()
    blocks: set[str] = set(gold_ids)
    lines: set[tuple[str, int]] = set()
    for sess in task.sessions:
        if sess.session_id not in gold_ids:
            continue
        file, start, end = _span_loc(sess)
        if file:
            files.add(file)
            if start is not None and end is not None:
                lines |= expand_line_set(file, start, end)
    return {"file": files, "block": blocks, "line": lines}


def _predicted_sets(record: PerTaskRecord, task: Task) -> dict[str, set]:
    """The agent's predicted {file, block, line} sets from its declared retrieval.

    "Declared" == the FINAL retrieve step's item ids (``last_only=True``), the
    ContextBench convention for the predicted-context set. Each predicted block
    id is mapped back to its span Session (so we recover file + line range) via
    the task's session table. Ids that don't resolve to a known span still count
    at block granularity (they are spurious retrievals — they HURT precision).
    """
    pred_ids = retrieved_item_ids(record.trajectory, last_only=True)
    sess_by_id = {s.session_id: s for s in task.sessions}
    files: set[str] = set()
    blocks: set[str] = set()
    lines: set[tuple[str, int]] = set()
    for iid in pred_ids:
        blocks.add(iid)
        sess = sess_by_id.get(iid)
        if sess is None:
            continue
        file, start, end = _span_loc(sess)
        if file:
            files.add(file)
            if start is not None and end is not None:
                lines |= expand_line_set(file, start, end)
    return {"file": files, "block": blocks, "line": lines}


def _memory_token_overhead(record: PerTaskRecord) -> float:
    """Memory-token overhead ratio ``memory_tokens / total_tokens`` for one task.

    A harness token-accounting signal (LOWER is better). NOT the paper's
    Efficiency metric (that is AUC-Cov, Eq. 5 — see :func:`_auc_cov`). ``0.0``
    when no generate tokens were spent (nothing to divide).
    """
    traj = record.trajectory
    total = traj.total_tokens
    if total <= 0:
        return 0.0
    return traj.memory_tokens / total


def _step_block_snapshots(record: PerTaskRecord) -> list[set]:
    """Per-step retrieved block sets ``[C_1^A, C_2^A, ...]`` (Eq. 4 inputs).

    Each retrieve step's ``item_id`` set is one snapshot ``C_t^A`` (block
    granularity == the span/element ids ContextBench keys on). Order follows the
    trajectory's step order (chronological observation order). Empty snapshots
    (a retrieve step that returned nothing) are preserved so ``T`` matches the
    number of observation steps the agent actually took.
    """
    out: list[set] = []
    for step in record.trajectory.steps:
        if step.kind != "retrieve":
            continue
        out.append({str(ri.item_id) for ri in step.retrieved})
    return out


def _cumulative_unions(snapshots: Sequence[set]) -> list[set]:
    """``[A^(1), A^(2), ...]`` where ``A^(t) = ∪_{i≤t} C_i^A`` (Eq. 4)."""
    cum: list[set] = []
    acc: set = set()
    for snap in snapshots:
        acc = acc | snap
        cum.append(set(acc))
    return cum


def _auc_cov(snapshots: Sequence[set], gold: set) -> Optional[float]:
    """Efficiency / AUC-Cov (Eq. 5): ``(1/T)·Σ_t Recall(A^(t), C^G)``.

    Normalized area under the cumulative gold-coverage curve (HIGHER is better).
    Returns ``None`` (skip this task) when there are no retrieve snapshots
    (``T == 0``) so the metric degrades gracefully instead of fabricating ``0``.
    Recall is ``0.0`` against empty gold by the shared :func:`set_recall`
    convention; the per-task value is still well-defined.
    """
    if not snapshots:
        return None
    cum = _cumulative_unions(snapshots)
    return sum(set_recall(a_t, gold) for a_t in cum) / len(cum)


def _redundancy(snapshots: Sequence[set]) -> Optional[float]:
    """Redundancy (Eqs. 6-7), LOWER is better.

    ``(1/(T-1))·Σ_{t=2..T} |C_t ∩ (∪_{i<t} C_i)| / |C_t|``. Defined only for
    ``T ≥ 2`` (needs at least one prior snapshot); returns ``None`` otherwise
    (offline single-step trajectories are skipped). A step with an EMPTY ``C_t``
    contributes ``0`` to the per-step ratio (no elements to be redundant) and is
    counted in the ``T-1`` denominator, matching "fraction of elements in C_t
    already seen" with the empty-set convention ``0/0 -> 0``.
    """
    snaps = list(snapshots)
    if len(snaps) < 2:
        return None
    prev: set = set(snaps[0])
    ratios: list[float] = []
    for t in range(1, len(snaps)):
        c_t = snaps[t]
        if c_t:
            ratios.append(len(c_t & prev) / len(c_t))
        else:
            ratios.append(0.0)
        prev = prev | c_t
    return sum(ratios) / len(ratios)


def _evidence_drop(
    snapshots: Sequence[set], final_pred: set, gold: set
) -> Optional[float]:
    """Evidence Drop (Eqs. 8-10), LOWER is better.

    ``Drop = 1 - |C^A ∩ C^G| / |G_seen|`` where ``G_seen = (∪_t C_t^A) ∩ C^G``
    is the gold evidence observed at least once during the trajectory and ``C^A``
    is the FINAL/declared patch context (``final_pred``). Returns ``None`` (skip)
    when ``G_seen`` is empty (no gold was ever observed, so "drop" is undefined).
    """
    if not snapshots or not gold:
        return None
    seen_union: set = set()
    for snap in snapshots:
        seen_union |= snap
    g_seen = seen_union & gold
    if not g_seen:
        return None
    keep = len(final_pred & gold) / len(g_seen)
    return 1.0 - keep


def _count_by_lang(records: Sequence[PerTaskRecord], by_id: dict[str, Task]) -> dict[str, int]:
    """Task counts per language (component ``n``)."""
    out: dict[str, int] = {}
    for r in records:
        t = by_id.get(r.task_id)
        lang = (t.competency if t is not None else None) or "unknown"
        out[lang] = out.get(lang, 0) + 1
    return out


def _as_int(value: Any) -> Optional[int]:
    """Best-effort int coercion (gold-span bounds may arrive as str/float)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


__all__ = ["ContextBenchNativeEvaluator"]
