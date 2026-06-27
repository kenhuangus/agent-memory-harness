"""Claude Code hook handler — fires Daydream on Stop / PreCompact.

Claude Code fires hooks at lifecycle points (``SessionStart``, ``UserPromptSubmit``,
``Stop``, ``PreCompact``, …) and passes a JSON payload on stdin. This handler is the
single entry point the plugin's ``hooks.json`` routes every event to.

Behavior:

* On ``Stop`` / ``PreCompact``: shell out to ``python -m memeval.dreaming.cli daydream`` via
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
``memeval.dreaming.cli`` so sync-PreCompact + manual invocations get a visible
signal that the runtime environment is broken.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
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

_OUTPUT_TAIL_CHARS = 4000


def _build_subprocess_env(settings: Settings) -> dict[str, str]:
    """Return the minimum-surface env for the daydream subprocess (halliday F4)."""
    env = {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS}
    if settings.store_path is not None:
        env["MEMORY_STORE"] = str(settings.store_path)
    return env


def _daydream_command() -> list[str]:
    """Invoke daydream through this hook's interpreter, avoiding PATH drift."""
    return [sys.executable, "-m", "memeval.dreaming.cli", "daydream"]


def _text_tail(value: object) -> str:
    """Return a bounded text tail for child-process diagnostics."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[-_OUTPUT_TAIL_CHARS:]


def _payload_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    """Return non-secret hook-payload facts useful for debugging daydream no-ops."""
    transcript_raw = payload.get("transcript_path")
    transcript_path = str(transcript_raw) if transcript_raw is not None else ""
    diag: dict[str, Any] = {
        "payload_keys": sorted(str(key) for key in payload.keys()),
        "has_session_id": bool(payload.get("session_id")),
        "has_transcript_path": bool(transcript_path),
    }
    if transcript_path:
        diag["transcript_path"] = transcript_path
        try:
            diag["transcript_exists"] = Path(transcript_path).is_file()
        except OSError:
            diag["transcript_exists"] = False
    return diag


def _fire_daydream_subprocess(
    event_name: str, payload: dict[str, Any], settings: Settings, events: EventStream
) -> None:
    """Shell out to the daydream CLI module; fail-open per ADR-harness-006."""
    payload_diag = _payload_diagnostics(payload)
    if not payload_diag["has_session_id"] or not payload_diag["has_transcript_path"]:
        events.emit(
            "daydream.hook_payload_incomplete",
            session_id=settings.session_id,
            hook=event_name,
            **payload_diag,
        )
    try:
        completed = subprocess.run(
            _daydream_command(),
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
            "cookbook-memory: could not launch memeval.dreaming.cli with "
            f"{sys.executable} — install with `pip install -e eval[daydream]` "
            "to enable memory extraction.\n"
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
        returncode=completed.returncode,
        stdout_tail=_text_tail(completed.stdout),
        stderr_tail=_text_tail(completed.stderr),
        **payload_diag,
    )


#: Plugin-side recall INJECTION (Option B), OFF by default. When $MEMORY_INJECT_RECALL
#: is truthy, the UserPromptSubmit hook recalls relevant memories itself and injects
#: them into the turn's context (Claude Code ``additionalContext``) — so the agent gets
#: memory WITHOUT having to choose to call the recall tool. Unset -> byte-identical to
#: the historical no-context behavior. $MEMORY_INJECT_RECALL_K caps hits (default 5).
#: Cost: builds the store + runs a query embed on every UserPromptSubmit; under the
#: accuracy/Voyage profile that is a network call + cold store open per turn —
#: acceptable for the experiment; a resident recall daemon would amortize it later.
_INJECT_TOGGLE_KEY = "MEMORY_INJECT_RECALL"
_INJECT_K_KEY = "MEMORY_INJECT_RECALL_K"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _inject_enabled() -> bool:
    return (os.environ.get(_INJECT_TOGGLE_KEY) or "").strip().lower() in _TRUTHY


def _inject_k() -> int:
    try:
        return max(1, int(os.environ.get(_INJECT_K_KEY, "5")))
    except (TypeError, ValueError):
        return 5


def _recall_injection(
    payload: dict[str, Any], settings: Settings, events: EventStream
) -> Optional[dict[str, Any]]:
    """Recall memories for the submitted prompt and return a UserPromptSubmit
    ``additionalContext`` response that injects them — or ``None`` (fail-open) when
    there is no prompt, no store, or nothing relevant. Reuses the SAME recall path as
    the MCP ``recall`` tool, so the injected memories match what the agent would get,
    and the recall is recorded in the events stream like any other recall."""
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return None
    try:
        from ...core.client import MemoryClient

        client = MemoryClient(
            store=(str(settings.store_path) if settings.store_path else None),
            session_id=settings.session_id,
            events=events,
        )
        hits = client.recall(prompt, k=_inject_k())
    except Exception:  # noqa: BLE001 — fail-open: never break the turn
        return None
    lines = [
        f"- {(h.content or '').strip()}" for h in hits if (h.content or "").strip()
    ]
    if not lines:
        return None
    context = (
        "Relevant memories from past work on THIS project (auto-recalled — "
        "use where they apply):\n" + "\n".join(lines)
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def handle(event_name: str, payload: dict[str, Any], *, store: Optional[str] = None) -> dict[str, Any]:
    """Process one hook event; return the hook response dict.

    Always emits a ``note`` event naming the hook (preserved from the pre-PR
    behavior). On ``Stop`` / ``PreCompact``, additionally shells out to
    ``python -m memeval.dreaming.cli daydream`` per ADR-001. On ``UserPromptSubmit``,
    when ``$MEMORY_INJECT_RECALL`` is set, recalls relevant memories and injects them
    into the turn via ``additionalContext`` (Option B). Otherwise returns ``{}`` — no
    ``additionalContext``, no decision, no session interference (the historical default).
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
    if event_name == "UserPromptSubmit" and _inject_enabled():
        injected = _recall_injection(payload, settings, events)
        if injected:
            return injected
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
