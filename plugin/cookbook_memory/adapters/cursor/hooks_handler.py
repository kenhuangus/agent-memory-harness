"""Cursor CLI hook handler — fires the Daydreamer on ``sessionEnd`` / ``preCompact``.

The Cursor-side counterpart of
:mod:`cookbook_memory.adapters.claude_code.hooks_handler`. Cursor fires hooks at
lifecycle points and passes a JSON payload on stdin; this handler is the single entry
point the plugin's ``hooks/hooks.json`` (and the harness's user-level
``$HOME/.cursor/hooks.json``) routes every event to.

Behavior (mirrors the Claude handler, with Cursor semantics):

* On ``sessionEnd`` / ``preCompact``: shell out to ``python -m memeval.dreaming.cli
  daydream`` — the SAME harness-agnostic Daydreamer the Claude path uses — so memory
  is written from the just-ended turn's transcript. ``sessionEnd`` is Cursor's
  turn-complete analog of Claude Code's ``Stop`` (verified to fire in headless
  ``--print`` and to carry ``transcript_path`` + ``session_id``).
* On every other event (``sessionStart`` / ``postToolUse`` / …): record a ``note``
  event and return ``{}``.

THE ONE CURSOR-SPECIFIC PIECE — transcript normalization. The Daydreamer's transcript
reader (``memeval.dreaming.transcript_formatter``) parses **Claude Code's** JSONL line
shape (``{"type": ..., "message": {"role", "content": [blocks]}}``). Cursor writes a
different shape (``{"role": "user"|"assistant", "message": [blocks]}`` + a
``turn_ended`` marker). So before invoking the Daydreamer we **normalize** Cursor's
transcript into the shape the formatter expects, write it to a temp file, and pass it
via ``daydream-cli --log`` — leaving the shared Daydreamer + formatter (the dreaming
domain's code) untouched. The content blocks themselves (``{type:text|tool_use|
thinking}``) are already the shape ``render_blocks`` handles, so only the line
envelope needs rewriting.

Fail-open per ADR-harness-006: any subprocess / IO exception is caught, recorded, and
``handle()`` still returns ``{}``. ``KeyboardInterrupt`` / ``SystemExit`` propagate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from ...core.config import Settings
from ...core.events import EventStream

#: Cursor events that trigger a Daydream write (the ``Stop`` / ``PreCompact`` analogs).
_GATED_EVENTS = frozenset({"sessionEnd", "preCompact"})

_ALLOWED_ENV_KEYS = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LOGNAME", "USER", "TMPDIR",
    "MEMORY_STORE", "OPENROUTER_API_KEY", "DREAM_PROVIDER", "DREAM_MODEL",
    "DREAM_RETENTION_DAYS", "DREAM_SWEEP_INTERVAL_MIN",
})

_TIMEOUT_BY_EVENT = {"sessionEnd": 600, "preCompact": 120}
_OUTPUT_TAIL_CHARS = 4000


def _build_subprocess_env(settings: Settings) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS}
    if settings.store_path is not None:
        env["MEMORY_STORE"] = str(settings.store_path)
    return env


def _daydream_command(log_path: Optional[Path], session_id: Optional[str]) -> list[str]:
    """Invoke the Daydreamer through this hook's interpreter (avoids PATH drift).

    Passes the NORMALIZED transcript via ``--log`` and the session via ``--session``
    so the daydream CLI does not have to read Cursor's native (incompatible)
    ``transcript_path`` shape from stdin."""
    cmd = [sys.executable, "-m", "memeval.dreaming.cli", "daydream"]
    if log_path is not None:
        cmd += ["--log", str(log_path)]
    if session_id:
        cmd += ["--session", str(session_id)]
    return cmd


def _normalize_transcript(cursor_path: Path, out_dir: Path) -> Optional[Path]:
    """Rewrite a Cursor transcript JSONL into the shape the Daydreamer's formatter
    expects, returning the new file path (or ``None`` if nothing usable).

    Cursor writes the role at the TOP level and ``message`` as EITHER a content-block
    list OR a dict carrying ``content`` (the shape has varied across versions — verified
    both forms), e.g.:
        ``{"role": "user", "message": {"content": [<blocks>]}}``   (current)
        ``{"role": "user", "message": [<blocks>]}``                (older)
    Daydreamer wants:
        ``{"type": <role>, "message": {"role": <role>, "content": [<blocks>]}}``

    Non-message lines (e.g. ``{"type": "turn_ended"}``) are passed through unchanged —
    the formatter tolerates/handles unknown line types. Malformed lines are skipped.
    Fail-open: any IO error returns ``None`` (the caller then skips the write)."""
    try:
        raw_lines = cursor_path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    out_lines: list[str] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        role = d.get("role")
        msg = d.get("message")
        content = None
        if role in ("user", "assistant"):
            if isinstance(msg, list):                       # older shape
                content = msg
            elif isinstance(msg, dict) and isinstance(msg.get("content"), list):
                content = msg["content"]                     # current shape
        if content is not None:
            out_lines.append(json.dumps({
                "type": role,
                "message": {"role": role, "content": content},
            }))
        else:
            # Pass other lines through (turn_ended, system, etc.) — harmless to the
            # formatter, preserves any future fields.
            out_lines.append(raw)
    if not out_lines:
        return None
    out_path = out_dir / "cursor_transcript_normalized.jsonl"
    try:
        out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    except OSError:
        return None
    return out_path


def _payload_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    transcript_raw = payload.get("transcript_path")
    transcript_path = str(transcript_raw) if transcript_raw is not None else ""
    # Cursor uses conversation_id; session_id is added at runtime — accept either.
    sid = payload.get("session_id") or payload.get("conversation_id")
    diag: dict[str, Any] = {
        "payload_keys": sorted(str(k) for k in payload.keys()),
        "has_session_id": bool(sid),
        "has_transcript_path": bool(transcript_path),
    }
    if transcript_path:
        diag["transcript_path"] = transcript_path
        try:
            diag["transcript_exists"] = Path(transcript_path).is_file()
        except OSError:
            diag["transcript_exists"] = False
    return diag


def _text_tail(value: object) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[-_OUTPUT_TAIL_CHARS:]


def _fire_daydream_subprocess(
    event_name: str, payload: dict[str, Any], settings: Settings, events: EventStream
) -> None:
    """Normalize the Cursor transcript, then shell out to the Daydreamer; fail-open."""
    diag = _payload_diagnostics(payload)
    session_id = payload.get("session_id") or payload.get("conversation_id")
    transcript_path = payload.get("transcript_path")
    # Cursor's sessionEnd payload routinely carries `transcript_path: null` (verified) —
    # the path isn't ready at hook time. Without a transcript there is nothing to
    # extract, and invoking daydream-cli anyway makes it try to open the literal string
    # "None" (FileNotFoundError) — noise, not work. So SKIP cleanly here; the harness's
    # synchronous drain backstop (which discovers the transcript itself) does the write.
    # The hook still does useful work when a transcript IS present (e.g. interactive/IDE
    # use, where sessionEnd carries it).
    if not transcript_path or not Path(str(transcript_path)).is_file():
        events.emit("daydream.hook_skipped_no_transcript", session_id=settings.session_id,
                    hook=event_name, **diag)
        return
    log_path: Optional[Path] = None
    tmpdir: Optional[tempfile.TemporaryDirectory] = None
    tmpdir = tempfile.TemporaryDirectory(prefix="cbmem-cursor-")
    log_path = _normalize_transcript(Path(str(transcript_path)), Path(tmpdir.name))
    try:
        completed = subprocess.run(
            _daydream_command(log_path, session_id),
            input=json.dumps(payload),
            capture_output=True, text=True,
            timeout=_TIMEOUT_BY_EVENT[event_name],
            env=_build_subprocess_env(settings), check=False,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except FileNotFoundError as exc:
        sys.stderr.write(
            "cookbook-memory (cursor): could not launch memeval.dreaming.cli with "
            f"{sys.executable} — install `pip install -e eval[daydream]` to enable "
            "memory extraction.\n"
        )
        events.emit("daydream.hook_subprocess_failed", session_id=settings.session_id,
                    hook=event_name, error_class=type(exc).__name__)
        return
    except Exception as exc:
        events.emit("daydream.hook_subprocess_failed", session_id=settings.session_id,
                    hook=event_name, error_class=type(exc).__name__)
        return
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
    events.emit("daydream.hook_subprocess_fired", session_id=settings.session_id,
                hook=event_name, returncode=completed.returncode,
                stdout_tail=_text_tail(completed.stdout),
                stderr_tail=_text_tail(completed.stderr), **diag)


def handle(event_name: str, payload: dict[str, Any], *, store: Optional[str] = None) -> dict[str, Any]:
    """Process one Cursor hook event; return the hook response dict (always ``{}``)."""
    settings = Settings.from_env(
        store=store, session_id=payload.get("session_id") or payload.get("conversation_id"),
    )
    events = EventStream(settings.events_path)
    events.emit("note", session_id=settings.session_id, hook=event_name)
    if event_name in _GATED_EVENTS:
        _fire_daydream_subprocess(event_name, payload, settings, events)
    return {}


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: ``hooks_handler <eventName>``; reads the payload JSON from stdin.

    Always exits ``0`` (fail-open). Wired from ``hooks.json`` as
    ``python -m cookbook_memory.adapters.cursor.hooks_handler <eventName>``."""
    argv = sys.argv[1:] if argv is None else argv
    event_name = argv[0] if argv else "unknown"
    # Load the repo-root .env so the daydream subprocess inherits OPENROUTER_API_KEY /
    # DREAM_* (it filters env to _ALLOWED_ENV_KEYS). Fail-open: never break the hook.
    try:
        from memeval.dotenv_loader import load_root_dotenv
        load_root_dotenv()
    except Exception:
        pass
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {}
    try:
        handle(event_name, payload)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
