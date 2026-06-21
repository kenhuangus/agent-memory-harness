"""Daydream events shim — ADR-dreaming-009.

PR2 ships a logging-only stub. The full local-diary writer
(``${MEMORY_STORE%/*}/dream/<session_id>.daydream-events.jsonl`` per
ADR-009) lands in PR3 alongside the Daydream engine's session-id
context (PR4 supplies the session_id at invocation time).

The API surface is the eventual one: when PR3 ships the diary writer,
this implementation swaps; call sites do not change.

Fail-open: emission is best-effort. A logging failure does not propagate
to the caller (per ADR-005 §Consequences "events emission" + ADR-006
fail-open posture). The stdlib logger is robust enough that wrapping
this in a try/except is paranoia, not policy — if logging itself
breaks, we have bigger problems than missed events.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

__all__ = ["emit"]


def emit(event_type: str, **fields: Any) -> None:
    """Record a Daydream event.

    PR2 implementation logs at DEBUG via stdlib ``logging``. PR3 swaps
    in the local-diary writer per ADR-dreaming-009; call sites unchanged.

    Args:
        event_type: short event name (e.g. ``"llm_unavailable"``,
            ``"llm_call_failed"``, ``"redaction.chunk"``).
        **fields: arbitrary structured kwargs (provider, model, reason,
            status, counts, etc.) — captured in the log message in PR2,
            written as JSON keys in PR3's diary.
    """
    _logger.debug("event %s %s", event_type, fields)
