"""Claude Code hook handler — fires Daydream on Stop / PreCompact.

Claude Code fires hooks at lifecycle points (``SessionStart``, ``UserPromptSubmit``,
``Stop``, ``PreCompact``, …) and passes a JSON payload on stdin. This handler is the
single entry point the plugin's ``hooks.json`` routes every event to.

Behavior:

* On ``Stop`` / ``PreCompact``: shell out to ``daydream-cli daydream`` via
  ``subprocess.run`` (subprocess preserves the plugin's import-isolation seam —
  heavy deps like ``detect-secrets``/``httpx`` stay out of the hook process).
  Stdin is the verbatim payload as JSON; env is the minimum-surface allowlist.
  Stop's subprocess timeout is 600s; PreCompact's is 120s (sync hook; shorter
  ceiling so compaction doesn't block on a long-running daydream).
* On every other event (``SessionStart`` / ``UserPromptSubmit`` / ``PostCompact``
  / …): records a ``note`` event and returns ``{}`` (the pre-PR behavior is
  preserved as a regression guard).
* Every event also emits the pre-existing ``note`` observation; gated events
  additionally emit ``daydream.hook_subprocess_fired`` (on success) or
  ``daydream.hook_subprocess_failed`` (on any caught exception).

Fail-open per ADR-harness-006: subprocess exceptions (``TimeoutExpired``,
``FileNotFoundError``, ``CalledProcessError``, any other ``Exception``) are
caught, recorded, and ``handle()`` still returns ``{}``. ``KeyboardInterrupt``
and ``SystemExit`` propagate so tests + sync PreCompact have a clean cancel
path. On ``FileNotFoundError`` specifically, a one-line stderr message names
``daydream-cli`` so sync-PreCompact + manual invocations get a visible signal
that PATH is broken.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Optional

from ...core.config import Settings
from ...core.events import EventStream


_GATED_EVENTS = frozenset({"Stop", "PreCompact"})


_ALLOWED_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "USER",
    "TMPDIR",
    "MEMORY_STORE",
    "OPENROUTER_API_KEY",
    "DREAM_PROVIDER",
    "DREAM_MODEL",
    "DREAM_RETENTION_DAYS",
    "DREAM_SWEEP_INTERVAL_MIN",
})


_TIMEOUT_BY_EVENT = {
    "Stop": 600,
    "PreCompact": 120,
}


def _build_subprocess_env(settings: Settings) -> dict[str, str]:
    """Return the minimum-surface env for the daydream-cli subprocess (halliday F4)."""
    env = {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS}
    if settings.store_path is not None:
        env["MEMORY_STORE"] = str(settings.store_path)
    return env


def _fire_daydream_subprocess(
    event_name: str, payload: dict[str, Any], settings: Settings, events: EventStream
) -> None:
    """Shell out to daydream-cli daydream; fail-open per ADR-harness-006."""
    try:
        subprocess.run(
            ["daydream-cli", "daydream"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_BY_EVENT[event_name],
            env=_build_subprocess_env(settings),
            check=False,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except FileNotFoundError as exc:
        sys.stderr.write(
            "cookbook-memory: daydream-cli not on PATH — install with "
            "`pip install -e eval[daydream]` to enable memory extraction.\n"
        )
        events.emit(
            "daydream.hook_subprocess_failed",
            session_id=settings.session_id,
            hook=event_name,
            error_class=type(exc).__name__,
        )
        return
    except Exception as exc:
        events.emit(
            "daydream.hook_subprocess_failed",
            session_id=settings.session_id,
            hook=event_name,
            error_class=type(exc).__name__,
        )
        return
    events.emit(
        "daydream.hook_subprocess_fired",
        session_id=settings.session_id,
        hook=event_name,
    )


def handle(event_name: str, payload: dict[str, Any], *, store: Optional[str] = None) -> dict[str, Any]:
    """Process one hook event; return the hook response dict.

    Always emits a ``note`` event naming the hook (preserved from the pre-PR
    behavior). On ``Stop`` / ``PreCompact``, additionally shells out to
    ``daydream-cli daydream`` per ADR-001. Returns ``{}`` either way — no
    ``additionalContext``, no decision, no session interference.
    """
    settings = Settings.from_env(
        store=store, session_id=payload.get("session_id"),
    )
    events = EventStream(settings.events_path)
    events.emit(
        "note",
        session_id=settings.session_id,
        hook=event_name,
    )
    if event_name in _GATED_EVENTS:
        _fire_daydream_subprocess(event_name, payload, settings, events)
    return {}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: ``hooks_handler <EventName>``; reads the payload JSON from stdin.

    Always exits ``0`` (fail-open). Wired from ``hooks.json`` as
    ``python -m cookbook_memory.adapters.claude_code.hooks_handler <EventName>``.
    """
    argv = sys.argv[1:] if argv is None else argv
    event_name = argv[0] if argv else "unknown"
    # Load the repo-root .env so the daydream subprocess this hook fires inherits
    # OPENROUTER_API_KEY / DREAM_* (it filters env to _ALLOWED_ENV_KEYS) and can actually
    # extract memories. Fail-open: env loading must never break the hook.
    try:
        from memeval.dotenv_loader import load_root_dotenv
        load_root_dotenv()
    except Exception:  # noqa: BLE001
        pass
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
