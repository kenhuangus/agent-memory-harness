"""The structured memory-events stream (ADR-harness-007).

Every memory operation — ``recall``, ``remember``, ``dream``, and any ``error`` —
appends one JSON line to an events log under ``$MEMORY_STORE``. The stream is the
plugin's externally-observable output: it doubles as the machine-readable surface a
black-box eval reads to verify "what got remembered" without importing plugin
internals, and as the debugging trail behind the ``memory log`` / ``memory stats``
commands.

The event shape is deliberately span-friendly (operation, ids, timing,
parent/child via ``meta``) so a future Langfuse exporter is a sink swap, not a
re-instrumentation. Writing is best-effort and never raises into a caller: an
events-stream failure must not break a user's session (ADR-harness-006).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import ensure_store_gitignore

#: Recognized operation names for an event's ``op`` field.
OPS = ("recall", "remember", "dream", "error")


def event(
    op: str,
    *,
    session_id: Optional[str] = None,
    ids: Optional[list[str]] = None,
    query: Optional[str] = None,
    summary: Optional[str] = None,
    scope: Optional[str] = None,
    ts: float = 0.0,
    **meta: Any,
) -> dict[str, Any]:
    """Build one event record.

    ``ts`` is supplied by the caller (the harness clock) so the core stays
    deterministic and free of wall-clock reads; ``0.0`` means "unstamped". Extra
    keyword arguments are folded into ``meta`` for span-friendly context.
    """
    rec: dict[str, Any] = {"ts": ts, "op": op}
    if scope is not None:
        rec["scope"] = scope
    if session_id is not None:
        rec["session_id"] = session_id
    rec["ids"] = list(ids or [])
    if query is not None:
        rec["query"] = query
    if summary is not None:
        rec["summary"] = summary
    rec["meta"] = dict(meta)
    return rec


class EventStream:
    """Append-only JSONL sink for memory events under ``$MEMORY_STORE``.

    ``path`` is the events file (created on first write). All writes are
    best-effort: any I/O error is swallowed so the stream can never break the
    caller's turn (ADR-harness-006). A ``None`` path makes every emit a no-op,
    which is what the fail-open / not-configured path uses.
    """

    def __init__(self, path: Optional[str | Path]) -> None:
        self.path = Path(path) if path else None

    def emit(self, op: str, **fields: Any) -> dict[str, Any]:
        """Build, append, and return one event (returned even if the write fails)."""
        rec = event(op, **fields)
        self._append(rec)
        return rec

    def _append(self, rec: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # The events stream is the first writer into a fresh store, so it also
            # scaffolds the store's .gitignore (ADR-harness-017); no-op once present.
            ensure_store_gitignore(self.path.parent)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:
            # Best-effort: observability must never break the session.
            pass

    def read(self) -> list[dict[str, Any]]:
        """Return all events recorded so far (empty if the stream is absent)."""
        if self.path is None or not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out


__all__ = ["EventStream", "event", "OPS"]
