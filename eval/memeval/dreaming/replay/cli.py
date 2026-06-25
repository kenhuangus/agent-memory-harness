"""daydream-replay — fixture-grounded diagnostic harness for the daydream pipeline.

Replays a Claude Code session transcript (or several) through
:func:`memeval.dreaming.engine.daydream`, simulating production growth by
appending the fixture in line-aligned byte slices and letting the engine's
cursor mechanism walk the file naturally. Each replay slice gets one
``daydream()`` call; the per-slice cursor delta + diary event vocabulary lets
the operator observe which slices stall (issue-133 fingerprint = consecutive
``chunk_skipped_unavailable_llm`` events on the same pre-cursor).

Output is JSONL on stdout: one ``replay.run_start`` record, one
``replay.chunk`` per slice, one ``replay.fixture_summary`` per fixture, one
``replay.run_summary`` at end. ``DREAM_DEBUG=1`` (per PR #137) additionally
mirrors the engine's own diary events to stdout, interleaved with the replay
records — useful for full-pipeline debugging.

Pre-flight: requires ``OPENROUTER_API_KEY`` set unless ``client=`` is passed
programmatically (the test entry point). The CLI fails fast (exit 2) if the
key is unset — silent fail-open here would defeat the diagnostic purpose.

Per-fixture setup is destructive: the working log under ``--basedir`` is
truncated, the sidecar is unlinked, and the diary is unlinked before the
fixture's first slice — fresh cursor=0 every run. Use ``--session-id`` to
override the auto-derived id when chaining fixtures through one session
(the working log appends across fixtures; sidecar carries cursor forward).

Issue #133 surface — the production "zero saved items" symptom. Default
``--chunk-bytes 50000`` is for synthetic signature testing with a stub
client. To reproduce the actual production payload shape (one large delta
hitting OpenRouter 400 → fail-open without cursor advance → retried
indefinitely) against the real LLM, use ``--whole-fixture`` which emits the
entire fixture as one slice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from memeval.dreaming._state import sidecar_path, safe_session_stem
from memeval.dreaming.events import diary_path_for
from memeval.dreaming.llm import LLMClient
from memeval.protocols import MemoryStore

__all__ = ["main", "replay_fixtures", "slice_fixture", "classify_outcome", "Slice"]


# --------------------------------------------------------------------------- #
# Slicing — pure functions; no engine deps
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Slice:
    """One unit of fixture growth fed to the engine per daydream() call."""

    index: int
    byte_start: int
    byte_end: int
    line_start: int
    line_end: int
    bytes_: bytes  # field name avoids shadowing built-in `bytes`

    @property
    def size(self) -> int:
        return len(self.bytes_)


def slice_fixture(
    fixture: Path, *, chunk_bytes: int, whole_fixture: bool
) -> Iterator[Slice]:
    """Yield line-aligned :class:`Slice`s from ``fixture``.

    ``whole_fixture=True`` yields exactly one slice covering the entire file
    (the issue-#133 reproducer against a real LLM). Otherwise yields slices
    of at most ``chunk_bytes`` whole lines; a single JSONL line longer than
    ``chunk_bytes`` is emitted alone (never split mid-line) and its slice's
    size exceeds the requested chunk size — that's a real-world condition
    the engine has to handle and the replay must surface, not hide.
    """
    if chunk_bytes <= 0 and not whole_fixture:
        raise ValueError(f"chunk_bytes must be > 0, got {chunk_bytes}")

    data = fixture.read_bytes()
    if not data:
        return

    if whole_fixture:
        yield Slice(
            index=0,
            byte_start=0,
            byte_end=len(data),
            line_start=0,
            line_end=data.count(b"\n") + (0 if data.endswith(b"\n") else 1),
            bytes_=data,
        )
        return

    buf: list[bytes] = []
    buf_size = 0
    buf_byte_start = 0
    buf_line_start = 0
    line_no = 0
    byte_pos = 0
    slice_idx = 0
    for raw_line in data.splitlines(keepends=True):
        # Flush BEFORE adding this line if adding would push over the limit AND
        # we already have at least one line buffered. Avoids splitting mid-line.
        if buf and buf_size + len(raw_line) > chunk_bytes:
            yield Slice(
                index=slice_idx,
                byte_start=buf_byte_start,
                byte_end=byte_pos,
                line_start=buf_line_start,
                line_end=line_no,
                bytes_=b"".join(buf),
            )
            slice_idx += 1
            buf = []
            buf_size = 0
            buf_byte_start = byte_pos
            buf_line_start = line_no
        buf.append(raw_line)
        buf_size += len(raw_line)
        byte_pos += len(raw_line)
        line_no += 1

    if buf:
        yield Slice(
            index=slice_idx,
            byte_start=buf_byte_start,
            byte_end=byte_pos,
            line_start=buf_line_start,
            line_end=line_no,
            bytes_=b"".join(buf),
        )


# --------------------------------------------------------------------------- #
# Outcome classification — pure function over diary event types
# --------------------------------------------------------------------------- #
#: Event-type names imported as STRINGS (the engine + extract modules emit
#: them; pinning string constants here means a rename in the engine breaks
#: this module loudly via a test, not silently via mis-classification).
_EVT_CHUNK_ERROR = "daydream.chunk_error"
_EVT_LOCK_HELD = "daydream.dream_in_progress_skipped"
_EVT_SKIPPED_UNAVAIL = "chunk_skipped_unavailable_llm"
_EVT_SKIPPED_PARSE = "chunk_skipped_parse_failed"
_EVT_MEMORY_WRITTEN = "daydream.memory_written"


def classify_outcome(
    event_types: set[str],
    *,
    cursor_advanced: bool,
    engine_raised: bool = False,
) -> str:
    """Classify the outcome of one daydream call from its diary event bag.

    Priority order matters: a single call can emit several events; the most
    specific failure mode wins. ``no_advance_no_event`` is the
    whitespace-only-chunk path (engine returns at line 137-138 before any
    emit) — distinct from ``skipped_llm_unavailable`` which is the issue-#133
    fingerprint.

    ``engine_raised`` wins over the diary-derived classification: engine.daydream
    is fail-open by contract (ADR-006) and any exception escaping it is a
    contract violation. Misclassifying that as the benign whitespace-only path
    would hide a real bug, so it surfaces as its own ``engine_raised`` outcome
    above all others (CodeRabbit finding on PR #142).
    """
    if engine_raised:
        return "engine_raised"
    if _EVT_CHUNK_ERROR in event_types:
        return "chunk_error"
    if _EVT_LOCK_HELD in event_types:
        return "skipped_lock_held"
    if _EVT_SKIPPED_PARSE in event_types:
        return "skipped_parse_failed"
    if _EVT_SKIPPED_UNAVAIL in event_types:
        return "skipped_llm_unavailable"
    if _EVT_MEMORY_WRITTEN in event_types:
        return "wrote_memories"
    if cursor_advanced:
        return "advanced_no_items"
    return "no_advance_no_event"


# --------------------------------------------------------------------------- #
# Sidecar + diary observation helpers
# --------------------------------------------------------------------------- #
def _read_cursor(basedir: Path, session_id: str) -> int:
    """Return the current sidecar cursor for ``session_id``, or 0 if absent."""
    target = sidecar_path(basedir, session_id)
    if not target.exists():
        return 0
    try:
        return int(json.loads(target.read_text())["cursor"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return 0


def _read_diary_records_since(
    diary: Path, byte_offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Return diary records appended since ``byte_offset`` + the new EOF offset.

    Tolerates a torn trailing line (partial JSON at EOF) — happens if the
    engine crashed mid-write. Returns whatever records are fully-formed.
    """
    if not diary.exists():
        return [], byte_offset
    raw = diary.read_bytes()
    new_eof = len(raw)
    if new_eof <= byte_offset:
        return [], new_eof
    chunk = raw[byte_offset:new_eof]
    records: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn trailing line — skip
    return records, new_eof


# --------------------------------------------------------------------------- #
# Run state — per fixture + run-level aggregator
# --------------------------------------------------------------------------- #
@dataclass
class _Issue133Run:
    """One run of consecutive ``skipped_llm_unavailable`` outcomes on the
    same pre-cursor. The strongest production-#133 fingerprint."""

    fixture: str
    stuck_cursor: int
    start_chunk: int
    consecutive_skips: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "stuck_cursor": self.stuck_cursor,
            "start_chunk": self.start_chunk,
            "consecutive_skips": self.consecutive_skips,
        }


@dataclass
class _FixtureSummary:
    fixture: str
    session_id: str
    chunks_total: int = 0
    chunks_advanced: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    items_written: int = 0
    wall_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "replay.fixture_summary",
            "fixture": self.fixture,
            "session_id": self.session_id,
            "chunks_total": self.chunks_total,
            "chunks_advanced": self.chunks_advanced,
            "outcomes": dict(self.outcomes),
            "items_written": self.items_written,
            "wall_ms": round(self.wall_ms, 3),
        }


# --------------------------------------------------------------------------- #
# Programmatic entry point — tests call this directly with a stub client
# --------------------------------------------------------------------------- #
def replay_fixtures(
    *,
    fixtures: list[Path],
    basedir: Path,
    chunk_bytes: int,
    whole_fixture: bool,
    max_chunks: int | None,
    shared_session_id: str | None,
    client: LLMClient,
    store: MemoryStore,
    out: Any = sys.stdout,
) -> int:
    """Replay ``fixtures`` through engine.daydream, emit JSONL to ``out``.

    Returns the run-summary record's ``exit_code`` (0 on completion). The
    CLI ``main()`` is a thin wrapper that builds the production OpenRouter
    client + InMemoryStore; tests invoke this entry point with a
    deterministic stub client.
    """
    from memeval.dreaming import engine  # lazy: keep import-time deps light

    (basedir / "dream").mkdir(parents=True, exist_ok=True)

    def _emit(record: dict[str, Any]) -> None:
        out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        out.flush()

    _emit({
        "event_type": "replay.run_start",
        "ts": time.time(),
        "fixture_count": len(fixtures),
        "chunk_bytes": chunk_bytes,
        "whole_fixture": whole_fixture,
        "max_chunks": max_chunks,
        "shared_session_id": shared_session_id,
        "basedir": str(basedir),
        "client_model": getattr(client, "model", "unknown"),
    })

    fixture_summaries: list[_FixtureSummary] = []
    issue_133_runs: list[_Issue133Run] = []

    for fixture in fixtures:
        session_id = safe_session_stem(
            shared_session_id if shared_session_id is not None else fixture.stem
        )
        working_log = basedir / f"replay-{session_id}.jsonl"
        sidecar = sidecar_path(basedir, session_id)
        diary = diary_path_for(basedir, session_id)

        # Fresh-cursor setup — DESTRUCTIVE; explicit ordering matters:
        # sidecar unlink BEFORE working-log truncation prevents the engine
        # from briefly seeing cursor > file_size (which would trigger the
        # rotation path and pollute the diary).
        if shared_session_id is None:
            sidecar.unlink(missing_ok=True)
            diary.unlink(missing_ok=True)
            working_log.write_bytes(b"")

        fix_sum = _FixtureSummary(fixture=str(fixture), session_id=session_id)
        active_run: _Issue133Run | None = None
        slices_seen = 0
        t_fixture_start = time.monotonic()

        for sl in slice_fixture(
            fixture, chunk_bytes=chunk_bytes, whole_fixture=whole_fixture
        ):
            if max_chunks is not None and slices_seen >= max_chunks:
                break
            slices_seen += 1

            # Append slice to growing working log + fsync (durability before
            # engine reads). The engine reads via fp.seek(cursor) so the
            # newly-appended bytes are visible on the very next call.
            with open(working_log, "ab") as fp:
                fp.write(sl.bytes_)
                fp.flush()
                os.fsync(fp.fileno())

            cursor_before = _read_cursor(basedir, session_id)
            diary_size_before = diary.stat().st_size if diary.exists() else 0
            t0 = time.monotonic()
            engine_exc: str | None = None
            try:
                engine.daydream(
                    session_id=session_id,
                    log_path=working_log,
                    store=store,
                    client=client,
                    basedir=basedir,
                )
            except Exception as exc:  # engine is fail-open by contract; defensive
                engine_exc = f"{type(exc).__name__}: {exc}"
            wall_ms = (time.monotonic() - t0) * 1000.0
            cursor_after = _read_cursor(basedir, session_id)
            new_records, _ = _read_diary_records_since(diary, diary_size_before)

            # All new diary records since diary_size_before belong to THIS
            # daydream call — replay is single-process + sequential, so the
            # byte-offset window already scopes attribution. (Some engine
            # events — notably chunk_skipped_unavailable_llm + chunk_skipped_
            # parse_failed in _extract.py — don't carry chunk_id, so filtering
            # by chunk_id would drop the very events that fingerprint #133.)
            event_types = {r["event_type"] for r in new_records}
            items_written = sum(
                1 for r in new_records if r["event_type"] == _EVT_MEMORY_WRITTEN
            )
            outcome = classify_outcome(
                event_types,
                cursor_advanced=(cursor_after > cursor_before),
                engine_raised=engine_exc is not None,
            )

            fix_sum.chunks_total += 1
            if cursor_after > cursor_before:
                fix_sum.chunks_advanced += 1
            fix_sum.outcomes[outcome] = fix_sum.outcomes.get(outcome, 0) + 1
            fix_sum.items_written += items_written
            fix_sum.wall_ms += wall_ms

            # Issue-#133 run aggregator: STRICTLY gated on
            # outcome == skipped_llm_unavailable AND unchanged pre_cursor.
            # Anything else closes an active run.
            if outcome == "skipped_llm_unavailable":
                if active_run is not None and active_run.stuck_cursor == cursor_before:
                    active_run.consecutive_skips += 1
                else:
                    if active_run is not None:
                        issue_133_runs.append(active_run)
                    active_run = _Issue133Run(
                        fixture=str(fixture),
                        stuck_cursor=cursor_before,
                        start_chunk=sl.index,
                    )
            else:
                if active_run is not None:
                    issue_133_runs.append(active_run)
                    active_run = None

            _emit({
                "event_type": "replay.chunk",
                "fixture": str(fixture),
                "session_id": session_id,
                "chunk_index": sl.index,
                "boundary": "whole_fixture" if whole_fixture else "lines",
                "byte_range": [sl.byte_start, sl.byte_end],
                "line_range": [sl.line_start, sl.line_end],
                "bytes_in_chunk": sl.size,
                "cursor_before": cursor_before,
                "cursor_after": cursor_after,
                "outcome": outcome,
                "items_written": items_written,
                "diary_event_types": sorted(event_types),
                "wall_ms": round(wall_ms, 3),
                "engine_exc": engine_exc,
            })

        if active_run is not None:
            issue_133_runs.append(active_run)
        fixture_summaries.append(fix_sum)
        _emit(fix_sum.to_dict())

    _emit({
        "event_type": "replay.run_summary",
        "ts": time.time(),
        "fixtures_count": len(fixtures),
        "chunks_total": sum(f.chunks_total for f in fixture_summaries),
        "chunks_advanced": sum(f.chunks_advanced for f in fixture_summaries),
        "items_written_total": sum(f.items_written for f in fixture_summaries),
        "outcomes_total": _sum_outcomes(fixture_summaries),
        "issue_133_runs": [r.to_dict() for r in issue_133_runs],
        "wall_ms_total": round(sum(f.wall_ms for f in fixture_summaries), 3),
    })
    return 0


def _sum_outcomes(summaries: list[_FixtureSummary]) -> dict[str, int]:
    total: dict[str, int] = {}
    for s in summaries:
        for k, v in s.outcomes.items():
            total[k] = total.get(k, 0) + v
    return total


# --------------------------------------------------------------------------- #
# CLI entry point — argparse + production LLM client wiring
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="daydream-replay",
        description=(
            "Replay a Claude Code transcript fixture through the daydream "
            "pipeline; emit JSONL diagnostic records on stdout. Reproduces "
            "the production zero-items symptom (issue #133) when "
            "--whole-fixture is used against a real LLM."
        ),
    )
    p.add_argument(
        "--fixture", type=Path, action="append", required=True,
        help="path to a fixture JSONL (repeatable)",
    )
    p.add_argument(
        "--chunk-bytes", type=int, default=50_000,
        help="target slice size (line-aligned); ignored when --whole-fixture",
    )
    p.add_argument(
        "--whole-fixture", action="store_true",
        help="feed the entire fixture as ONE slice (the #133 reproducer)",
    )
    p.add_argument(
        "--max-chunks", type=int, default=None,
        help="cap on slices per fixture (default: no cap)",
    )
    p.add_argument(
        "--session-id", type=str, default=None,
        help="shared session id across all fixtures (default: per-fixture stem)",
    )
    p.add_argument(
        "--basedir", type=Path, default=None,
        help="basedir for sidecar/diary/working-log (default: tempdir)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Pre-flight: require OPENROUTER_API_KEY in the CLI path. The engine
    # fail-opens silently when the key is unset (ADR-012); that defeats the
    # diagnostic. Tests bypass via the programmatic replay_fixtures() entry.
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.stderr.write(
            "daydream-replay: OPENROUTER_API_KEY is unset. The replay tool "
            "is intentionally fail-loud here because the engine's silent "
            "fail-open would mask the very symptom this tool exists to "
            "diagnose. Set OPENROUTER_API_KEY in .env or your shell and "
            "re-run.\n"
        )
        return 2

    # Validate fixtures up front; argparse will have set args.fixture to a
    # list of Path objects but we want an explicit early failure on a
    # mistyped path rather than a confusing tempdir-side error mid-replay.
    for f in args.fixture:
        if not f.is_file():
            sys.stderr.write(f"daydream-replay: fixture not found: {f}\n")
            return 1

    from memeval.dreaming.llm import make_client
    from memeval.harness import InMemoryStore

    client = make_client()
    store = InMemoryStore()
    basedir = (
        Path(args.basedir).resolve() if args.basedir is not None
        else Path(tempfile.mkdtemp(prefix="daydream-replay-"))
    )

    return replay_fixtures(
        fixtures=args.fixture,
        basedir=basedir,
        chunk_bytes=args.chunk_bytes,
        whole_fixture=args.whole_fixture,
        max_chunks=args.max_chunks,
        shared_session_id=args.session_id,
        client=client,
        store=store,
    )


if __name__ == "__main__":
    raise SystemExit(main())
