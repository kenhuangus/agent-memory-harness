"""Claude Code hook handler — fail-open lifecycle observation (walking skeleton).

Claude Code fires hooks at lifecycle points (``SessionStart``, ``UserPromptSubmit``,
``Stop``, ``PreCompact``, …) and passes a JSON payload on stdin. This handler is the
single entry point the plugin's ``hooks.json`` routes every event to.

In the walking skeleton every hook is a **fail-open no-op**: it records the event to
the memory events stream (so behavior is observable — ADR-harness-007) and exits ``0``
without altering the session. The substantive behaviors land in later slices and slot
in here behind the same dispatch:

* ``Stop`` / ``PreCompact`` → the Daydreamer day pass (S4a).
* ``UserPromptSubmit`` / ``SessionStart`` / ``PostCompact`` → supplementary top-k
  injection / re-inject (S6).

The handler **never** raises into Claude Code: any error is swallowed and the hook
still exits ``0``, so a memory failure can never break the user's turn
(ADR-harness-006).
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from ...core.config import Settings
from ...core.events import EventStream


def handle(event_name: str, payload: dict, *, store: Optional[str] = None) -> dict:
    """Process one hook event; return the (empty) hook response dict.

    Records a ``note`` event naming the hook, then returns ``{}`` — no
    ``additionalContext``, no decision — i.e. a pure observation. The session
    proceeds unchanged. Substantive per-hook logic replaces the body here later.
    """
    settings = Settings.from_env(
        store=store, session_id=payload.get("session_id"),
    )
    events = EventStream(settings.events_path)
    events.emit(
        "note",
        session_id=settings.session_id,
        hook=event_name,
        phase="skeleton-noop",
    )
    return {}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: ``hooks_handler <EventName>``; reads the payload JSON from stdin.

    Always exits ``0`` (fail-open). Wired from ``hooks.json`` as
    ``python -m cookbook_memory.adapters.claude_code.hooks_handler <EventName>``.
    """
    argv = sys.argv[1:] if argv is None else argv
    event_name = argv[0] if argv else "unknown"
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    try:
        response = handle(event_name, payload)
    except Exception:
        # Fail-open: never break the session on a hook error.
        response = {}
    if response:
        json.dump(response, sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
