"""Shared recall/remember logic — the harness-agnostic core every adapter calls.

Both ``recall`` and ``remember`` route through the Orchestrator (ADR-harness-002),
emit a structured event (ADR-harness-007), and are **fail-open**: any error from the
Orchestrator degrades to a safe default (recall → empty, remember → empty id) and is
recorded as an ``error`` event rather than raised (ADR-harness-006). A memory failure
must never break the caller's turn.

The Claude Code MCP server, the ``memory`` CLI, and any future adapter all call this
module — so the recall/remember behavior is defined once and shared.
"""

from __future__ import annotations

from typing import Optional

from .events import EventStream
from .orchestrator import Hit, Orchestrator


class Memory:
    """Recall/remember over an :class:`Orchestrator`, with event emission.

    Construct one per process (the MCP server, a CLI invocation) from a resolved
    Orchestrator and an :class:`EventStream`. ``session_id`` tags emitted events so
    the black-box eval and the ``memory log`` command can attribute them.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        events: EventStream,
        *,
        session_id: Optional[str] = None,
        default_k: int = 5,
    ) -> None:
        self.orchestrator = orchestrator
        self.events = events
        self.session_id = session_id
        self.default_k = default_k

    def recall(
        self,
        query: str,
        k: Optional[int] = None,
        *,
        as_of: Optional[float] = None,
        ts: float = 0.0,
    ) -> list[Hit]:
        """Search memory; return ranked hits (empty on any failure, fail-open)."""
        kk = self.default_k if k is None else k
        try:
            hits = self.orchestrator.recall(query, k=kk, as_of=as_of)
        except Exception as exc:
            self.events.emit(
                "error", session_id=self.session_id, query=query, ts=ts,
                op_attempted="recall", error=str(exc),
            )
            return []
        self.events.emit(
            "recall", session_id=self.session_id, query=query, ts=ts,
            ids=[h.id for h in hits], k=kk, n=len(hits),
        )
        return hits

    def remember(
        self,
        content: str,
        *,
        tags: Optional[list[str]] = None,
        ts: float = 0.0,
    ) -> str:
        """Persist ``content`` and return its memory id ("" on failure, fail-open)."""
        try:
            mem_id = self.orchestrator.remember(content, tags=tags, timestamp=ts)
        except Exception as exc:
            self.events.emit(
                "error", session_id=self.session_id, ts=ts,
                op_attempted="remember", error=str(exc),
            )
            return ""
        self.events.emit(
            "remember", session_id=self.session_id, ts=ts,
            ids=[mem_id] if mem_id else [], tags=list(tags or []),
        )
        return mem_id


__all__ = ["Memory"]
