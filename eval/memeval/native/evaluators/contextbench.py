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
``line_recall/precision/f1``, plus **efficiency** (mean memory-token overhead
ratio ``memory_tokens / total_tokens`` — LOWER is better, the paper's retrieval
efficiency signal) and **avg_retrieved** (mean predicted-set size).

Components — one :class:`ComponentScore` per **language** (the loader's
``competency`` stratum), each carrying that slice's file/block/line F1s, plus a
per-granularity component (``granularity:file|block|line``) bundling recall +
precision + F1 so a dashboard can pivot either way.

Resolve rate (optional, Docker-gated, never hard-fails offline)
--------------------------------------------------------------
ContextBench is fundamentally a *retrieval* benchmark, but the rows carry SWE-
bench fields (``patch``/``f2p``/``p2p``), so a resolve-rate signal is offered as
a SECONDARY metric when a real grader is available. It is computed from the
per-task ``success`` flags ONLY when present; offline (EchoAgent + no Docker)
those are ``None`` and the metric degrades to ``n=0`` (skipped) — it NEVER calls
Docker or hard-fails. A real run can pass ``grader=memeval.grader.get_grader(
"swebench", on_unavailable="skip")`` through ``run`` to populate it.

Offline: EchoAgent + InMemoryStore + DeterministicJudge, stdlib only, no network,
no Docker.
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
                "granularity + efficiency; resolve_rate is a Docker-gated secondary "
                "signal, skipped offline (n=0)."
            ),
        )

        # Per-task per-granularity recall/precision (means feed the headline +
        # components). Accumulate parallel lists keyed by granularity.
        rec: dict[str, list[float]] = {g: [] for g in _GRANULARITIES}
        prec: dict[str, list[float]] = {g: [] for g in _GRANULARITIES}
        f1s: dict[str, list[float]] = {g: [] for g in _GRANULARITIES}
        effs: list[float] = []
        retrieved_sizes: list[float] = []
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

            effs.append(_efficiency(r))
            retrieved_sizes.append(float(len(pred["block"])))

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

        # efficiency (LOWER is better) + avg predicted-context size.
        report.add_metric(
            NativeMetric("efficiency", mean(effs), n=n, better="lower",
                         metadata={"formula": "mean(memory_tokens / total_tokens)"})
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
                            "when no Docker grader supplied (offline).",
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


def _efficiency(record: PerTaskRecord) -> float:
    """Memory-token overhead ratio ``memory_tokens / total_tokens`` for one task.

    Matches the harness efficiency metric. ``0.0`` when no generate tokens were
    spent (nothing to divide). LOWER is better.
    """
    traj = record.trajectory
    total = traj.total_tokens
    if total <= 0:
        return 0.0
    return traj.memory_tokens / total


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
