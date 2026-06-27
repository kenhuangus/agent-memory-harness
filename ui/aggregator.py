"""Parse a single run's basedir into the snapshot the dashboard renders.

Reads three surfaces:
- ``<basedir>/dream/<session>.daydream-events.jsonl`` — per-session daydream diaries
- ``<basedir>/events.jsonl`` — harness lifecycle + ``daydream.hook_subprocess_fired``
- ``<run_root>/swe_bench_cl-*.json`` — final-shape pipeline output (when present)

Every read tolerates an in-flight writer: partial JSON lines are dropped silently
so a polling client never sees a parse error on a file the bench is still appending to.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VOYAGE_429_MARK = "Voyage API error (HTTP 429)"
ACTIVE_WINDOW_S = 30  # last_activity younger than this -> "running"

# ---- recall telemetry (Monitor "Recalls" sub-tab) ----------------------------
# Pure visualization of already-emitted ``op=="recall"`` events; these constants
# change nothing about retrieval behavior or the event schema (ADR-harness-007).
RECALL_LIST_CAP = 200          # newest-first cap on the per-recall list in a snapshot
RECALL_SNIPPET_CHARS = 120     # per-hit content truncation; keeps the payload bounded
SCORE_HISTOGRAM_BUCKETS = 8    # fixed bucket count for the score-distribution sparkline
# DISPLAY-ONLY ⚠ reference: a recall whose BEST hit scores below the reference floor is
# flagged so the "weak match" pattern is visible. The reference is, per recall, the ACTUAL
# recall score floor recorded in the event (``meta.min_score``, stamped by the live
# RECALL_MIN_SCORE knob) — so the ⚠ band always matches what the live system drops at —
# falling back to DEFAULT_LOW_CONFIDENCE_FLOOR for runs that recorded no floor (historical
# data / floor disabled). 0.15 is the calibrated garbage/real split (n=52 accuracy recalls:
# garbage top ≤0.09, real ≥0.19); it replaces an earlier uncalibrated 0.30 guess that
# decoupled the view from the shipped 0.15 floor. Observability ONLY — never a retrieval
# decision (the real floor lives in RouterConfig.recall_min_score / contract.build_store).
DEFAULT_LOW_CONFIDENCE_FLOOR = 0.15


def discover_runs(results_root: Path) -> list[dict[str, Any]]:
    """Return one descriptor per ``results/<run>/_memory/.cookbook-memory`` basedir, newest first."""
    out: list[dict[str, Any]] = []
    for run_dir in sorted(results_root.iterdir()):
        base = run_dir / "_memory" / ".cookbook-memory"
        if not base.is_dir():
            continue
        last = _last_activity_ts(base)
        store_count = _store_item_count(base / "memory.db")
        # Cheap diary fallback so the dropdown's memory hint is honest when the
        # db is locked (in-flight writer).
        diary_count = _quick_diary_memory_count(base / "dream") if store_count is None else None
        out.append({
            "id": run_dir.name,
            "label": _format_label(run_dir.name),
            "basedir": str(base),
            "last_activity_ts": last,
            "last_activity_age_s": (time.time() - last) if last else None,
            "is_active": bool(last and (time.time() - last) < ACTIVE_WINDOW_S),
            "memories": store_count if store_count is not None else (diary_count or 0),
        })
    out.sort(key=lambda r: r["last_activity_ts"] or 0, reverse=True)
    return out


def snapshot(run_dir: Path) -> dict[str, Any]:
    """Full snapshot for one run. Returns a structure suitable for direct JSON serialization."""
    base = run_dir / "_memory" / ".cookbook-memory"
    if not base.is_dir():
        return {"error": f"no basedir under {run_dir}", "id": run_dir.name}

    diary_dir = base / "dream"
    diary_files = sorted(diary_dir.glob("*.daydream-events.jsonl")) if diary_dir.is_dir() else []
    sidecar_files = list(diary_dir.glob("*.json")) if diary_dir.is_dir() else []
    lock_files = list(diary_dir.glob("*.lock")) if diary_dir.is_dir() else []

    diary = _aggregate_diaries(diary_files)
    harness = _aggregate_harness(base / "events.jsonl")
    run_json = _latest_run_json(run_dir)
    store_count = _store_item_count(base / "memory.db")

    # Treat memory.db's `items` table as the source of truth for "memories stored."
    # The diary's `daydream.memory_written` count can lag (in-flight WAL) or be silently
    # zero (a known regression on some shas where events.py muted emits even though
    # store.write succeeded — see ADR-dreaming-XXX). Reconciliation surfaced via
    # `emit_drift` so the dashboard can flag the gap.
    kept = store_count if store_count is not None else diary["memory_written_count"]
    rejected = diary["candidate_rejected_count"]
    candidates = kept + rejected
    keep_rate = (kept / candidates) if candidates else None
    emit_drift = (store_count is not None) and (store_count != diary["memory_written_count"])

    return {
        "id": run_dir.name,
        "label": _format_label(run_dir.name),
        "basedir": str(base),
        "as_of": time.time(),
        "last_activity": {
            "ts": diary["last_event_ts"],
            "age_s": (time.time() - diary["last_event_ts"]) if diary["last_event_ts"] else None,
            "is_active": bool(diary["last_event_ts"] and (time.time() - diary["last_event_ts"]) < ACTIVE_WINDOW_S),
        },
        "metrics": {
            "memory": {
                "memories_written": kept,
                "memories_from_store": store_count,
                "memories_from_diary": diary["memory_written_count"],
                "emit_drift": emit_drift,
                "candidates_total": candidates,
                "candidates_rejected": rejected,
                "keep_rate": keep_rate,
                "noise_filter_engaged": diary["noise_filtered_count"] > 0,
            },
            "sessions": {
                "diaries": len(diary_files),
                "sidecars": len(sidecar_files),
                "locks": len(lock_files),
                "tasks_n": run_json.get("n_tasks"),
                "tasks_resolved": run_json.get("resolved"),
                "tasks_graded": run_json.get("graded_n"),
                "tasks_ungraded": run_json.get("ungraded"),
            },
            "cost": {
                "cost_usd": run_json.get("cost_usd"),
                "tokens_in": run_json.get("tokens_in"),
                "tokens_out": run_json.get("tokens_out"),
                "budget_usd": run_json.get("budget_usd"),
            },
            "failures": {
                "voyage_429": harness["voyage_429"],
                "chunk_errors": diary["chunk_error_count"],
                "hook_subprocess_failed": harness["hook_subprocess_failed"],
                "claude_timeouts": run_json.get("claude_timeouts", 0),
                "preflight_warnings": run_json.get("preflight_warnings", []),
                "run_warnings": run_json.get("warnings", []),
            },
        },
        "charts": {
            "cumulative_memories": diary["cumulative_memories"],
            "per_session_yield": diary["per_session_yield"],
            "hook_vs_emit": {
                "hook_fired": harness["hook_subprocess_fired"],
                "cli_resolved": diary["cli_resolved_count"],
                "llm_call": diary["llm_call_count"],
                "llm_call_succeeded": diary["llm_call_succeeded_count"],
                "chunk_extracted": diary["chunk_extracted_count"],
                "memory_written": kept,
            },
            "event_breakdown": diary["event_types"],
        },
        "recent_memories": diary["recent_memories"] or _recent_from_store(base / "memory.db"),
        "reject_top": diary["reject_top"],
        "reject_distinct": diary["reject_distinct"],
        # Read side: what got recalled. Same events.jsonl the harness already reads.
        # Additive — does not alter any existing snapshot key.
        "recalls": _aggregate_recalls(base / "events.jsonl"),
        "pipeline": run_json.get("pipeline_meta") or {},
    }


# ---- report export (Monitor "Print Report") ---------------------------------
# One-click export of a single run. The JSON report is the ``snapshot`` dict
# verbatim; ``report_markdown`` is its human-readable view. Pure formatter — it
# reads only the snapshot dict, adds no data source, and changes no behavior.


def report_markdown(snap: dict[str, Any]) -> str:
    """Render a ``snapshot(run_dir)`` dict as a readable Markdown report.

    Covers run/pipeline meta, memory health, sessions, cost, the recall summary
    (count, avg hits, score median/histogram, low-confidence count, and a table
    of recalls with query/n/top-score), failures, top reject reasons, and recent
    memories. Defensive against missing keys so a partial/early snapshot still
    renders. An error snapshot renders a one-line error report."""
    if snap.get("error"):
        return f"# Report — error\n\n`{snap.get('id', '?')}`: {snap['error']}\n"

    m = snap.get("metrics") or {}
    mem = m.get("memory") or {}
    sess = m.get("sessions") or {}
    cost = m.get("cost") or {}
    fail = m.get("failures") or {}
    pipe = snap.get("pipeline") or {}
    recalls = snap.get("recalls") or {}

    out: list[str] = []
    out.append(f"# Memory run report — {snap.get('label') or snap.get('id', '?')}")
    out.append("")
    out.append(f"- **run id:** `{snap.get('id', '—')}`")
    out.append(f"- **basedir:** `{snap.get('basedir', '—')}`")
    out.append(f"- **generated:** {_fmt_ts(snap.get('as_of'))}")
    la = snap.get("last_activity") or {}
    activity = _fmt_ts(la.get("ts"))
    if la.get("age_s") is not None:
        activity += f" ({_fmt_age(la.get('age_s'))})"
    if la.get("is_active"):
        activity += " · active"
    out.append(f"- **last activity:** {activity}")

    # Pipeline ----------------------------------------------------------------
    out.append("")
    out.append("## Pipeline")
    out.append("")
    pipe_rows = [(k, pipe.get(k)) for k in
                 ("benchmark", "sequence", "stage", "model", "version", "git_sha", "n_tasks")
                 if pipe.get(k) is not None]
    if pipe_rows:
        for k, v in pipe_rows:
            out.append(f"- **{k}:** {v}")
    else:
        out.append("_no pipeline metadata_")

    # Memory health -----------------------------------------------------------
    out.append("")
    out.append("## Memory health")
    out.append("")
    out.append(f"- **memories written:** {_num(mem.get('memories_written'))}")
    out.append(f"- **candidates:** {_num(mem.get('candidates_total'))} "
               f"({_num(mem.get('candidates_rejected'))} rejected)")
    out.append(f"- **keep rate:** {_pct(mem.get('keep_rate'))}")
    out.append(f"- **noise filter:** {'engaged' if mem.get('noise_filter_engaged') else 'off'}")
    if mem.get("emit_drift"):
        out.append(f"- **⚠ emit drift:** store={_num(mem.get('memories_from_store'))} "
                   f"vs diary={_num(mem.get('memories_from_diary'))}")

    # Sessions ----------------------------------------------------------------
    out.append("")
    out.append("## Sessions")
    out.append("")
    diaries = f"- **diaries:** {_num(sess.get('diaries'))}"
    if sess.get("tasks_n") is not None:
        diaries += f" of {_num(sess.get('tasks_n'))} tasks"
    out.append(diaries)
    out.append(f"- **resolved:** {_num(sess.get('tasks_resolved'))}")
    out.append(f"- **graded:** {_num(sess.get('tasks_graded'))} "
               f"(ungraded {_num(sess.get('tasks_ungraded'))})")
    out.append(f"- **sidecars:** {_num(sess.get('sidecars'))} · "
               f"**locks:** {_num(sess.get('locks'))}")

    # Cost --------------------------------------------------------------------
    out.append("")
    out.append("## Cost")
    out.append("")
    spend = f"- **spend:** {_usd(cost.get('cost_usd'))}"
    if cost.get("budget_usd"):
        spend += f" of {_usd(cost.get('budget_usd'))} budget"
    out.append(spend)
    out.append(f"- **tokens:** in {_num(cost.get('tokens_in'))} · "
               f"out {_num(cost.get('tokens_out'))}")

    # Recall summary ----------------------------------------------------------
    out.append("")
    out.append("## Recall summary")
    out.append("")
    stats = recalls.get("score_stats") or {}
    out.append(f"- **recalls:** {_num(recalls.get('count'))} "
               f"({_num(recalls.get('empty_count'))} empty)")
    avg = recalls.get("avg_hits")
    avg_line = f"- **avg hits:** {('%.1f' % avg) if isinstance(avg, (int, float)) else '—'}"
    if recalls.get("k"):
        avg_line += f" / k={recalls.get('k')}"
    out.append(avg_line)
    out.append(f"- **score median:** {_score(stats.get('median'))} "
               f"(min {_score(stats.get('min'))} · max {_score(stats.get('max'))})")
    floors = recalls.get("applied_floors") or []
    profiles = recalls.get("profiles") or []
    if profiles:
        out.append(f"- **profile:** {', '.join(profiles)}")
    if floors:
        out.append(f"- **recall score floor (live):** {', '.join(_score(f) for f in floors)}")
    thr = recalls.get("low_confidence_threshold")
    ref = (f"recorded floor, else {thr}" if floors else f"{thr}")
    out.append(f"- **low-confidence recalls:** {_num(recalls.get('low_confidence_count'))}"
               + (f" (top score < {ref}, display-only)" if thr is not None else ""))
    hist = stats.get("histogram") or []
    if hist:
        out.append(f"- **score histogram:** `{_histogram_ascii(hist)}`")

    rlist = recalls.get("recalls") or []
    if rlist:
        out.append("")
        out.append("| query | n | top score | ⚠ |")
        out.append("| --- | ---: | ---: | :-: |")
        for r in rlist:
            q = _cell(r.get("query"))
            warn = "⚠" if r.get("low_confidence") else ""
            out.append(f"| {q} | {_num(r.get('n'))} | {_score(r.get('top_score'))} | {warn} |")
        if recalls.get("truncated"):
            out.append("")
            out.append(f"_table capped at {len(rlist)} of "
                       f"{_num(recalls.get('count'))} recalls (newest first)._")
    else:
        out.append("")
        out.append("_no recalls recorded this run._")

    # Failures ----------------------------------------------------------------
    out.append("")
    out.append("## Failures")
    out.append("")
    out.append(f"- **voyage 429:** {_num(fail.get('voyage_429'))}")
    out.append(f"- **chunk errors:** {_num(fail.get('chunk_errors'))}")
    out.append(f"- **hook subprocess failed:** {_num(fail.get('hook_subprocess_failed'))}")
    out.append(f"- **claude timeouts:** {_num(fail.get('claude_timeouts'))}")
    for label, key in (("preflight warnings", "preflight_warnings"),
                       ("run warnings", "run_warnings")):
        warns = fail.get(key) or []
        if warns:
            out.append(f"- **{label} ({len(warns)}):**")
            for w in warns:
                out.append(f"  - {w}")

    # Top reject reasons ------------------------------------------------------
    out.append("")
    out.append("## Top reject reasons")
    out.append("")
    rejects = snap.get("reject_top") or []
    if rejects:
        out.append("| count | rationale |")
        out.append("| ---: | --- |")
        for r in rejects:
            out.append(f"| {_num(r.get('count'))} | {_cell(r.get('rationale'))} |")
        if snap.get("reject_distinct"):
            out.append("")
            out.append(f"_{snap['reject_distinct']} distinct rationales total._")
    else:
        out.append("_no rejected candidates this run._")

    # Recent memories ---------------------------------------------------------
    recent = snap.get("recent_memories") or []
    if recent:
        out.append("")
        out.append("## Recent memories")
        out.append("")
        out.append("| ts | session | tags | content |")
        out.append("| --- | --- | --- | --- |")
        for r in recent:
            tags = ", ".join(r.get("tags") or [])
            out.append(f"| {_fmt_ts(r.get('ts'))} | {_cell(r.get('session_short'))} | "
                       f"{_cell(tags)} | {_cell(r.get('content'))} |")

    out.append("")
    return "\n".join(out)


def _num(v: Any) -> str:
    """Thousands-separated int, or em-dash for None."""
    if v is None:
        return "—"
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _pct(r: Any) -> str:
    if not isinstance(r, (int, float)):
        return "—"
    return f"{r * 100:.1f}%"


def _usd(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"${v:.4f}" if v < 10 else f"${v:.2f}"


def _score(v: Any) -> str:
    """Recall scores are backend-dependent (BM25 vs cosine); show 2dp, no scaling."""
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:.2f}"


def _cell(s: Any) -> str:
    """One Markdown table cell: collapse newlines, escape pipes, fall back to em-dash."""
    text = "" if s is None else str(s)
    text = text.replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()
    return text or "—"


def _histogram_ascii(hist: list[int]) -> str:
    """Unicode sparkline over the score histogram (mirrors the monitor's spark)."""
    blocks = "▁▂▃▄▅▆▇█"
    mx = max(hist) if hist else 0
    if not mx:
        return " ".join("0" for _ in hist) or "—"
    return "".join(
        blocks[min(len(blocks) - 1, int(round((c / mx) * (len(blocks) - 1))))] if c else "·"
        for c in hist
    )


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(ts))) + " UTC"
    except (TypeError, ValueError, OSError):
        return "—"


def _fmt_age(s: Any) -> str:
    if s is None:
        return "—"
    try:
        s = int(s)
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h {(s % 3600) // 60}m ago"


# ---- internals -----------------------------------------------------------


def _aggregate_diaries(diary_files: list[Path]) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    memory_written: list[dict[str, Any]] = []
    yield_per_session: dict[str, dict[str, int]] = defaultdict(lambda: {"kept": 0, "rejected": 0})
    reject_rationales: Counter[str] = Counter()
    reject_samples: dict[str, dict[str, Any]] = {}
    last_ts: float = 0.0

    for d in diary_files:
        session = d.name.split(".")[0]
        for ev in _iter_jsonl(d):
            et = ev.get("event_type", "?")
            event_types[et] += 1
            ts = ev.get("ts")
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = float(ts)
            if et == "daydream.memory_written":
                yield_per_session[session]["kept"] += 1
                memory_written.append({
                    "ts": ts,
                    "session": session,
                    "session_short": session[:8],
                    "content": ev.get("content"),
                    "tags": ev.get("tags") or [],
                    "relevancy": ev.get("relevancy"),
                })
            elif et == "daydream.candidate_rejected":
                yield_per_session[session]["rejected"] += 1
                rationale = (ev.get("rationale") or ev.get("reason") or "—").strip()
                reject_rationales[rationale] += 1
                if rationale not in reject_samples:
                    reject_samples[rationale] = {
                        "content_snippet": ev.get("content_snippet") or "",
                        "session_short": session[:8],
                    }

    memory_written.sort(key=lambda m: m["ts"] or 0)
    cumulative = [
        {"ts": m["ts"], "count": i + 1} for i, m in enumerate(memory_written) if m["ts"]
    ]

    per_session = []
    for session, y in yield_per_session.items():
        if y["kept"] or y["rejected"]:
            per_session.append({
                "session": session,
                "session_short": session[:8],
                "kept": y["kept"],
                "rejected": y["rejected"],
            })
    per_session.sort(key=lambda r: (r["kept"], -r["rejected"]), reverse=True)

    return {
        "event_types": dict(event_types),
        "last_event_ts": last_ts or None,
        "cli_resolved_count": event_types.get("daydream.cli_resolved", 0),
        "noise_filtered_count": event_types.get("daydream.noise_filtered", 0),
        "llm_call_count": event_types.get("daydream.llm_call", 0),
        "llm_call_succeeded_count": event_types.get("llm_call_succeeded", 0),
        "chunk_extracted_count": event_types.get("daydream.chunk_extracted", 0),
        "memory_written_count": event_types.get("daydream.memory_written", 0),
        "candidate_rejected_count": event_types.get("daydream.candidate_rejected", 0),
        "chunk_error_count": (
            event_types.get("daydream.chunk_error", 0)
            + event_types.get("chunk_skipped_parse_failed", 0)
            + event_types.get("chunk_skipped_unavailable_llm", 0)
        ),
        "cumulative_memories": cumulative,
        "per_session_yield": per_session,
        "recent_memories": list(reversed(memory_written[-10:])),
        "reject_top": [
            {
                "rationale": r,
                "count": c,
                "sample": reject_samples.get(r, {}).get("content_snippet", "")[:200],
            }
            for r, c in reject_rationales.most_common(12)
        ],
        "reject_distinct": len(reject_rationales),
    }


def _aggregate_harness(events_path: Path) -> dict[str, Any]:
    fired = 0
    failed = 0
    voyage = 0
    hooks: Counter[str] = Counter()
    for ev in _iter_jsonl(events_path):
        op = ev.get("op", "")
        if op == "daydream.hook_subprocess_fired":
            fired += 1
            stderr_tail = (ev.get("meta") or {}).get("stderr_tail") or ""
            if VOYAGE_429_MARK in stderr_tail:
                voyage += 1
        elif op == "daydream.hook_subprocess_failed":
            failed += 1
        elif op == "note":
            hook = (ev.get("meta") or {}).get("hook")
            if hook:
                hooks[hook] += 1
    return {
        "hook_subprocess_fired": fired,
        "hook_subprocess_failed": failed,
        "voyage_429": voyage,
        "hooks": dict(hooks),
    }


def _aggregate_recalls(events_path: Path) -> dict[str, Any]:
    """Aggregate ``op=="recall"`` events for the monitor's Recalls sub-tab.

    Mirrors ``_aggregate_harness``: iterate the same ``events.jsonl``, match the op,
    fold matches into a render-ready dict. Read-only visualization of telemetry the
    plugin already emits — it does NOT change recall/retrieval behavior or the event
    schema (ADR-harness-007).

    Wire shape note: the events emitter folds extra kwargs into ``meta`` (see
    ``core/events.py``), so a recall line is
    ``{"ts", "op":"recall", "ids":[...], "query":..., "meta":{"k", "n", "hits":[...]}}``
    where each hit is ``{"id", "content", "score", "tokens", "rank", "timestamp"}``.
    An empty/failed recall emits ``meta.n == 0`` with no hits.
    """
    recalls: list[dict[str, Any]] = []   # file order (oldest first); reversed below
    all_scores: list[float] = []
    k_values: set[int] = set()
    hits_total = 0
    nonempty = 0
    empty = 0

    for ev in _iter_jsonl(events_path):
        # Fail-open on semantically-malformed-but-valid JSON: a non-dict event, a
        # non-dict meta, or a hits list holding non-dict entries must degrade to an
        # empty/partial aggregate, never crash snapshot().
        if not isinstance(ev, dict) or ev.get("op") != "recall":
            continue
        meta = ev.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        hits_raw = meta.get("hits")
        hits_raw = [h for h in hits_raw if isinstance(h, dict)] if isinstance(hits_raw, list) else []
        n = meta.get("n")
        if not isinstance(n, int):
            n = len(hits_raw)  # tolerate odd/legacy lines missing meta.n
        k = meta.get("k")
        if isinstance(k, int):
            k_values.add(k)

        hit_scores = [h.get("score") for h in hits_raw
                      if isinstance(h.get("score"), (int, float))]
        all_scores.extend(hit_scores)
        if n > 0:
            nonempty += 1
            hits_total += n
        else:
            empty += 1

        top_score = max(hit_scores) if hit_scores else None
        min_score = min(hit_scores) if hit_scores else None
        # The recall's ACTUAL config, recorded in the event (contract stamps these): the
        # active profile + the score floor that was applied. ``applied_floor`` drives the ⚠
        # reference so the view matches the live system; ``None`` (no floor recorded) falls
        # back to the calibrated default.
        profile = meta.get("profile") if isinstance(meta.get("profile"), str) else None
        af = meta.get("min_score")
        applied_floor = af if isinstance(af, (int, float)) and af > 0 else None
        # Display-only ⚠ flag; never a retrieval decision. See DEFAULT_LOW_CONFIDENCE_FLOOR.
        reference = applied_floor if applied_floor is not None else DEFAULT_LOW_CONFIDENCE_FLOOR
        low_confidence = top_score is not None and top_score < reference

        recalls.append({
            "query": ev.get("query"),
            "ts": ev.get("ts"),
            "n": n,
            "k": k,
            "profile": profile,
            "applied_floor": applied_floor,
            "top_score": top_score,
            "min_score": min_score,
            "low_confidence": low_confidence,
            "ids": list(ev.get("ids") or []),
            "hits": [
                {
                    "id": h.get("id"),
                    "score": h.get("score"),
                    "rank": h.get("rank"),
                    "snippet": _snippet(h.get("content")),
                }
                for h in hits_raw
            ],
        })

    recalls.reverse()  # newest first (ts is often unstamped, so use append order)
    total = len(recalls)
    truncated = total > RECALL_LIST_CAP
    capped = recalls[:RECALL_LIST_CAP]

    return {
        "count": total,
        "empty_count": empty,
        "avg_hits": (hits_total / nonempty) if nonempty else None,
        "k": next(iter(k_values)) if len(k_values) == 1 else None,
        "low_confidence_count": sum(1 for r in recalls if r["low_confidence"]),
        # The ⚠ reference: per recall it's the recorded floor (applied_floor); this is the
        # fallback for recalls with no recorded floor. Surfaced so the UI labels it honestly.
        "low_confidence_threshold": DEFAULT_LOW_CONFIDENCE_FLOOR,
        # The effective recall floor(s) actually recorded across this run's recalls (live
        # RECALL_MIN_SCORE), so the view shows the real retrieval config, not just the display ref.
        "applied_floors": sorted({r["applied_floor"] for r in recalls if r["applied_floor"] is not None}),
        "profiles": sorted({r["profile"] for r in recalls if r["profile"]}),
        "score_stats": _score_stats(all_scores),
        "recalls": capped,
        "truncated": truncated,
    }


def _snippet(content: Any, limit: int = RECALL_SNIPPET_CHARS) -> str:
    """Truncate hit content for the expand-row preview so the payload stays bounded."""
    s = ("" if content is None else str(content)).strip()
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def _score_stats(scores: list[float]) -> dict[str, Any]:
    """min/median/max/mean + a fixed-bucket histogram (sparkline) over all hit scores.

    Buckets span the observed [min, max] so the sparkline adapts to whatever scale a
    backend's scores happen to be on (BM25 vs cosine), rather than assuming [0, 1]."""
    if not scores:
        return {
            "count": 0, "min": None, "median": None, "max": None, "mean": None,
            "histogram": [], "bucket_edges": [],
        }
    lo = min(scores)
    hi = max(scores)
    buckets = [0] * SCORE_HISTOGRAM_BUCKETS
    if hi > lo:
        span = hi - lo
        for s in scores:
            idx = int((s - lo) / span * SCORE_HISTOGRAM_BUCKETS)
            buckets[min(idx, SCORE_HISTOGRAM_BUCKETS - 1)] += 1
        edges = [lo + span * i / SCORE_HISTOGRAM_BUCKETS
                 for i in range(SCORE_HISTOGRAM_BUCKETS + 1)]
    else:
        # All scores identical -> one populated bucket, degenerate edges.
        buckets[0] = len(scores)
        edges = [lo, hi]
    return {
        "count": len(scores),
        "min": lo,
        "median": statistics.median(scores),
        "max": hi,
        "mean": statistics.fmean(scores),
        "histogram": buckets,
        "bucket_edges": edges,
    }


def _latest_run_json(run_dir: Path) -> dict[str, Any]:
    """Read the most-recent ``<benchmark>-<ts>.json`` at the run root, if any."""
    candidates = sorted(
        (p for p in run_dir.glob("*.json") if not p.name.startswith("SUMMARY-")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return {}
    try:
        d = json.loads(candidates[0].read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, Any] = {}
    pipeline = d.get("pipeline") or {}
    out["pipeline_meta"] = {
        "version": pipeline.get("version"),
        "benchmark": pipeline.get("benchmark"),
        "sequence": pipeline.get("sequence"),
        "stage": pipeline.get("stage"),
        "model": pipeline.get("model"),
        "git_sha": (pipeline.get("git_sha") or "")[:7],
        "n_tasks": pipeline.get("n_tasks"),
        "started_at": pipeline.get("started_at"),
        "ended_at": pipeline.get("ended_at"),
    }
    runs = d.get("runs") or []
    if runs:
        r = runs[0]
        rel = r.get("reliability") or {}
        out["n_tasks"] = r.get("n_tasks")
        out["cost_usd"] = r.get("cost_usd")
        out["tokens_in"] = r.get("tokens_in")
        out["tokens_out"] = r.get("tokens_out")
        out["budget_usd"] = pipeline.get("budget_usd")
        out["resolved"] = rel.get("resolved")
        out["graded_n"] = rel.get("graded_n")
        out["ungraded"] = rel.get("ungraded")
        out["warnings"] = r.get("warnings") or []
        errs = rel.get("errors") or []
        out["claude_timeouts"] = sum(1 for e in errs if "Timeout" in (e.get("error") or ""))
    preflight = pipeline.get("preflight") or {}
    out["preflight_warnings"] = preflight.get("warnings") or []
    return out


def _quick_diary_memory_count(diary_dir: Path) -> int:
    """Cheap fallback: count ``daydream.memory_written`` lines via substring match
    rather than full JSON parsing. Used in the run-list when memory.db is locked."""
    if not diary_dir.is_dir():
        return 0
    total = 0
    for d in diary_dir.glob("*.daydream-events.jsonl"):
        try:
            with d.open("rb") as f:
                total += sum(1 for line in f if b'"daydream.memory_written"' in line)
        except OSError:
            continue
    return total


def _recent_from_store(db_path: Path) -> list[dict[str, Any]]:
    """Last-N memories straight from memory.db. Used as a fallback when the diary
    surface is silent (silent-emit regression) so the dashboard still shows real data."""
    if not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.25)
        try:
            rows = conn.execute(
                "SELECT timestamp, session_id, content, tags, relevancy FROM items "
                "ORDER BY timestamp DESC LIMIT 10"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for ts, sess, content, tags_json, rel in rows:
        try:
            tags = json.loads(tags_json) if tags_json else []
        except json.JSONDecodeError:
            tags = []
        out.append({
            "ts": ts,
            "session": sess or "—",
            "session_short": (sess or "—")[:8],
            "content": content,
            "tags": tags,
            "relevancy": rel,
        })
    return out


def _store_item_count(db_path: Path) -> int | None:
    """Read-only count of rows in memory.db's ``items`` table. Returns None when the
    db is missing, locked by an in-flight writer, or has no ``items`` table."""
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.25)
        try:
            row = conn.execute("SELECT COUNT(*) FROM items").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _iter_jsonl(path: Path):
    """Iterate JSON OBJECTS from a JSONL file, skipping partial trailing lines silently.

    Only dict records are yielded — a valid-JSON-but-non-object line (e.g. a bare
    string/number/list) is skipped, so every consumer (``_aggregate_harness`` /
    ``_aggregate_recalls`` / ``_aggregate_diaries``) can safely ``ev.get(...)`` without
    its own guard. This is the single fail-open boundary for malformed event lines."""
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def _last_activity_ts(base: Path) -> float | None:
    """Return the mtime of whichever of events.jsonl or any diary file was most recently written."""
    candidates: list[float] = []
    ev = base / "events.jsonl"
    if ev.is_file():
        candidates.append(ev.stat().st_mtime)
    diary_dir = base / "dream"
    if diary_dir.is_dir():
        try:
            candidates.append(max((p.stat().st_mtime for p in diary_dir.iterdir()), default=0))
        except OSError:
            pass
    return max(candidates) if candidates else None


def _format_label(run_id: str) -> str:
    """Compact human label: ``django · 8681435 · plugin-blank`` etc."""
    # vdjango_django_sequence-plugin-blank-8681435-1 -> django · 8681435 · plugin-blank
    name = run_id.lstrip("v")
    parts = name.split("-")
    if "_" in parts[0]:
        seq = parts[0].split("_")[0]
        rest = "-".join(parts[1:])
        return f"{seq} · {rest}"
    return name


# --------------------------------------------------------------------------- #
# Django results manifest (graphs view) — added 2026-06-27
#
# Scans `results/vdjango_django_sequence-*/` and returns one row per result
# directory with everything the graphs view needs. Re-scanned on every API
# call, so freshly-merged result dirs appear next refresh — no regen step.
#
# The daydream extraction-variant (V0..V5) is NOT recorded in pipeline output
# today; we infer it from SHA history. The mapping below mirrors KB-dreaming
# entry 14 + PR #238/#239 bodies. Folding the live variant into the pipeline
# writer would let us drop the override map.
# --------------------------------------------------------------------------- #

#: SHA -> daydream variant, inferred from .env state at run time. Only SHAs
#: with explicit evidence are populated; an unmapped SHA yields ``None`` so the
#: UI can render an honest "unknown" marker instead of guessing.
_DJANGO_DD_VARIANT_BY_SHA: dict[str, str] = {
    # V5 cohort: PR #239 evidence body + every run on or after the merge SHA
    "d68878c": "V5", "1763e51": "V5", "04c04d7": "V5", "2d80f9f": "V5",
    # V4 cohort: PR #238 evidence body, that SHA only
    "81378e9": "V4",
    # V2 cohort: KB-dreaming entry 14 documented V2 as the live variant on
    # 2026-06-25, covering every pre-PR-#238 SHA in the Django sequence.
    "a1677d1": "V2", "a4538fc": "V2", "818ccff": "V2", "8681435": "V2",
    "a6e4126": "V2", "4f03018": "V2", "c84be94": "V2",
}


def _safe_count(db_path: Path) -> int | None:
    try:
        import sqlite3
        con = sqlite3.connect(str(db_path)); con.row_factory = None
        n = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return None


def _django_dir_suffix(name: str) -> str | None:
    """`-accum` / `-blank` / `-2` etc. for the few results dirs that have one,
    used to distinguish plugin-dreamed seed runs at the same SHA."""
    # vdjango_django_sequence-plugin-dreamed-04c04d7-1-accum -> accum
    # vdjango_django_sequence-plugin-dreamed-1763e51-2       -> run2
    tail = name.rsplit("-", 2)
    if len(tail) < 2:
        return None
    suffix = tail[-1]
    if suffix in ("accum", "blank"):
        return suffix
    if suffix.isdigit() and suffix != "1":
        return f"run{suffix}"
    if suffix == "1":
        # Only label "run1" if there's also a "-2" sibling at the same SHA;
        # caller dedupes — leaving the disambiguation to render-time keeps
        # the schema simple.
        return None
    return None


def django_manifest(results_root: Path) -> list[dict[str, Any]]:
    """Return one manifest row per `results/vdjango_django_sequence-*/` directory.

    Re-scans the filesystem on each call — adding a new result directory + JSON
    is the only "regen" step. Rows are returned in newest-timestamp-first order.
    """
    rows: list[dict[str, Any]] = []
    if not results_root.is_dir():
        return rows
    for d in sorted(results_root.iterdir()):
        if not d.is_dir() or not d.name.startswith("vdjango_django_sequence-"):
            continue
        db = d / "_memory" / ".cookbook-memory" / "memory.db"
        n_mem = _safe_count(db) if db.exists() else 0
        jsons = sorted(d.glob("swe_bench_cl-*.json"))
        if not jsons:
            # incomplete dir (no benchmark output); skip — manifest is for results
            continue
        swe = jsons[-1]
        try:
            with swe.open() as f:
                data = json.load(f)
        except Exception:
            continue
        # A valid JSON file with the wrong shape (anything but a dict at the
        # top level) would crash later .get() calls and 500 the whole
        # /api/graphs/django request — skip those the same way we skip
        # malformed JSON, so one bad file never blanks the Graphs tab.
        if not isinstance(data, dict):
            continue
        p = data.get("pipeline") if isinstance(data.get("pipeline"), dict) else {}
        dream_cfg = p.get("dream", {}) if isinstance(p.get("dream"), dict) else {}
        dream_block = data.get("dream", {}) if isinstance(data.get("dream"), dict) else {}
        runs = data.get("runs") if isinstance(data.get("runs"), list) else []
        r0 = runs[0] if runs and isinstance(runs[0], dict) else {}
        tasks = r0.get("tasks") if isinstance(r0.get("tasks"), list) else []
        solved = sum(1 for t in tasks if t.get("resolved")) if tasks else None
        attempted = r0.get("n_tasks") or len(tasks)
        started = p.get("started_at")
        ended = p.get("ended_at")
        dur = round((ended - started) / 60, 1) if (started and ended) else None
        if "status" in dream_block:
            dream_outcome = "notrun"
            dream_counts = None
        elif "counts" in dream_block:
            c = dream_block["counts"]
            dream_outcome = "ran"
            dream_counts = {
                "retired": c.get("items_retired", 0),
                "pruned": c.get("items_pruned", 0),
                "contradicted": c.get("items_contradicted", 0),
                "calls": c.get("contradiction_llm_calls", 0),
                "must_known": c.get("items_must_known", 0),
            }
        else:
            dream_outcome = "notrun"
            dream_counts = None
        sha = p.get("git_sha") or ""
        seed = _django_dir_suffix(d.name)
        rows.append({
            "name": d.name,
            "ts": p.get("timestamp"),
            "sha": sha,
            "benchmark": p.get("benchmark") or "—",
            "stage": p.get("stage"),
            "harness": p.get("harness", "claude-code"),
            "agent": p.get("model"),
            # None when the SHA isn't in the inferred map — the UI renders an
            # explicit unknown marker rather than guessing V2 for every SHA
            # that hasn't been pinned by KB history.
            "ddVariant": _DJANGO_DD_VARIANT_BY_SHA.get(sha),
            "ddModel": (dream_cfg.get("model") or "deepseek/deepseek-v4-flash").replace("deepseek/", ""),
            "mem": n_mem,
            "dream": dream_outcome,
            "dreamCounts": dream_counts,
            "solved": solved,
            "attempted": attempted,
            "cost": r0.get("cost_usd"),
            "tokIn": r0.get("tokens_in"),
            "tokOut": r0.get("tokens_out"),
            "dur": dur,
            "budget": p.get("budget_usd") or 10.0,
            "seed": seed,
            "partial": (attempted or 0) < (p.get("n_tasks") or 50),
        })
    # Multi-runs at the same SHA + stage (e.g. plugin-dreamed-1763e51-1 and -2)
    # need explicit seed labels when neither dir name carries -accum/-blank.
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_key.setdefault((r["sha"], r["stage"]), []).append(r)
    for (_sha, _stage), group in by_key.items():
        if len(group) > 1 and all(r["seed"] is None for r in group):
            for i, r in enumerate(sorted(group, key=lambda x: x["ts"] or ""), start=1):
                r["seed"] = f"run{i}"
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows
