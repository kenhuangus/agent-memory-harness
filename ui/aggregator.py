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
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VOYAGE_429_MARK = "Voyage API error (HTTP 429)"
ACTIVE_WINDOW_S = 30  # last_activity younger than this -> "running"


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
        "pipeline": run_json.get("pipeline_meta") or {},
    }


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
    """Iterate JSON objects from a JSONL file, skipping partial trailing lines silently."""
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
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
