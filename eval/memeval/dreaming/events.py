"""Daydream events shim — ADR-dreaming-009 full implementation.

Records Daydream events to a local-only JSONL diary at
``<basedir>/dream/<session_id>.daydream-events.jsonl`` (gitignored per
ADR-009). The full impl replaces PR2's logging-only stub; the
``emit()`` call sites in :mod:`memeval.dreaming.llm` are unchanged.

Session/basedir binding via ``contextvars``
-------------------------------------------
The Daydream engine (PR4) wraps its per-invocation work in
:func:`event_context` (a context manager that binds ``session_id`` +
``basedir`` to ``ContextVar``s scoped to that invocation). ``emit()``
reads the ctxvars on every call:

- **Context bound:** write a JSONL line to the diary file (plus DEBUG
  log).
- **Context not bound:** DEBUG log only — graceful degradation for
  tests and any call site (like the OpenRouterClient constructed
  outside an engine context) that doesn't have a session.

This avoids plumbing ``session_id`` through every call site (the
PR2-era ``OpenRouterClient.complete()`` does not know about the
engine's session boundary and shouldn't have to).

Trust boundary + retention
--------------------------
Per ADR-011 §Consequences "Policy — local-only invariant" (mirror for
the events diary): the diary file is never read by any LLM call, never
transmitted, never logged remotely. It exists only on the local
filesystem. Retention is governed by ADR-dreaming-015 (uniform 30-day
TTL, swept on Daydream invocation; sweeper lands in PR4).

Migration to ADR-harness-007
----------------------------
When Keith's system-wide events stream → Langfuse ships
([`ADR-harness-007`](docs/adrs/ADR-harness-007-memory-events-stream.md)),
:func:`emit`'s body swaps to call into Keith's stream. Call sites do not
change. A successor ADR records the migration; the diary either stops
being written or stays as a local-debug mirror (decided then).

Fail-open
---------
Per ADR-005 §Consequences ("events emission") + ADR-006 + ADR-009:
diary-write errors do NOT propagate to the caller. A failed write logs
a WARNING and returns. Logging itself is robust enough that wrapping
the debug log in a try/except is paranoia, not policy.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

_logger = logging.getLogger(__name__)

__all__ = ["emit", "event_context", "diary_path_for"]


# --------------------------------------------------------------------------- #
# Per-invocation context (engine binds; emit reads)
# --------------------------------------------------------------------------- #
_session_id_var: ContextVar[str | None] = ContextVar(
    "daydream_session_id", default=None
)
_basedir_var: ContextVar[Path | None] = ContextVar(
    "daydream_basedir", default=None
)


@contextmanager
def event_context(*, session_id: str, basedir: str | Path) -> Iterator[None]:
    """Bind ``session_id`` + ``basedir`` for the duration of an engine call.

    The Daydream engine (PR4) wraps its chunk-extraction pass in this
    context manager; any ``emit()`` call inside writes to the
    session-scoped diary file. On exit, the previous values are
    restored (``ContextVar.reset()`` semantics) — works correctly with
    nested contexts and ``asyncio`` task isolation.
    """
    sid_token = _session_id_var.set(str(session_id))
    bd_token = _basedir_var.set(Path(basedir))
    try:
        yield
    finally:
        _session_id_var.reset(sid_token)
        _basedir_var.reset(bd_token)


def diary_path_for(basedir: str | Path, session_id: str) -> Path:
    """Compose the diary-file path for one session.

    ``<basedir>/dream/<session_id>.daydream-events.jsonl`` per ADR-009.
    The caller supplies ``basedir`` — full ``$MEMORY_STORE`` env-var
    resolution per ADR-015 is the engine's concern (PR4), not this
    helper's.
    """
    return Path(basedir) / "dream" / f"{session_id}.daydream-events.jsonl"


# --------------------------------------------------------------------------- #
# emit()
# --------------------------------------------------------------------------- #
def emit(event_type: str, **fields: Any) -> None:
    """Record a Daydream event.

    Always logs at DEBUG via stdlib ``logging``. If an
    :func:`event_context` is bound (``session_id`` + ``basedir`` set
    via ContextVars), also appends a JSONL line to the per-session
    diary file. Missing context → log-only fallback (no error).

    Fail-open: any exception during the diary write is caught, logged
    at WARNING, and suppressed. The caller never sees an event-related
    failure.
    """
    _logger.debug("event %s %s", event_type, fields)

    sid = _session_id_var.get()
    bd = _basedir_var.get()
    if sid is None or bd is None:
        return  # no context bound -- log-only

    record: dict[str, Any] = {
        "ts": time.time(),
        "event_type": event_type,
        **fields,
    }
    try:
        _write_diary_record(bd, sid, record)
    except Exception as exc:
        _logger.warning(
            "diary write failed for event %s (session=%s): %s",
            event_type,
            sid,
            exc,
        )


# --------------------------------------------------------------------------- #
# Diary writer (private)
# --------------------------------------------------------------------------- #
def _write_diary_record(basedir: Path, session_id: str, record: dict[str, Any]) -> None:
    """Append one JSONL record to the session diary.

    Creates the parent ``dream/`` directory if it doesn't exist.
    Append-only (``"a"`` mode); never truncates. Caller wraps in
    try/except for fail-open behavior.
    """
    target = diary_path_for(basedir, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with open(target, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")
