"""Locate and drive the **Claude Code CLI** headlessly, on macOS / Linux /
Windows / Windows-WSL.

``run_claude`` runs ``claude -p <prompt> --output-format json`` in a working
directory (optionally with an MCP config + allowed tools), parses the JSON
envelope, and returns the answer text plus token usage. Platform routing
(native vs WSL, with path translation) lives in :mod:`memeval.claudecode.platform`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import sandbox
from .platform import ClaudeRuntime, detect, to_wsl_path

#: Credentials stripped so the CLI uses the Claude Code *subscription* (OAuth),
#: never an API key — benchmarking Claude Code on its own auth, no API billing.
_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# Live transcript progress while a long `claude -p --output-format stream-json` run is
# blocking. Set to 0/off/false/no to disable, or a number of seconds to tune cadence.
_PROGRESS_ENV = "MEMEVAL_CLAUDE_PROGRESS_SECS"
_PROGRESS_DEFAULT_SECS = 20.0


class ClaudeNotInstalled(RuntimeError):
    """Raised when the ``claude`` CLI can't be found (native or in WSL)."""


@dataclass(slots=True)
class ClaudeResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def find_claude() -> Optional[str]:
    """Path to a usable ``claude`` (native exe, or the in-WSL path). ``None`` if absent."""
    rt = detect()
    return rt.exe if rt else None


def require_runtime(runtime: Optional[ClaudeRuntime] = None) -> ClaudeRuntime:
    rt = runtime or detect()
    if rt is None:
        raise ClaudeNotInstalled(
            "The Claude Code CLI was not found natively or in WSL. Install it with "
            "`npm install -g @anthropic-ai/claude-code` (macOS/Linux/Windows) or inside "
            "your WSL distro, then re-run. Overrides: $CLAUDE_CLI / $CLAUDE_WSL_DISTRO."
        )
    return rt


def _flags(
    *, model: Optional[str], mcp_config: Optional[str], allowed_tools: Optional[list[str]],
    append_system_prompt: Optional[str], permission_mode: str, strict_mcp: bool,
) -> list[str]:
    flags = ["--output-format", "json", "--permission-mode", permission_mode]
    if model:
        flags += ["--model", model]
    if mcp_config:
        flags += ["--mcp-config", mcp_config]
        if strict_mcp:
            flags += ["--strict-mcp-config"]
    if allowed_tools:
        flags += ["--allowedTools", ",".join(allowed_tools)]
    if append_system_prompt:
        flags += ["--append-system-prompt", append_system_prompt]
    return flags


def build_argv(
    runtime: ClaudeRuntime, prompt: str, *, cwd: str | Path,
    model: Optional[str] = None, mcp_config: Optional[str | Path] = None,
    allowed_tools: Optional[list[str]] = None, append_system_prompt: Optional[str] = None,
    permission_mode: str = "bypassPermissions", strict_mcp: bool = False,
    strip_api_key: bool = True, wsl_extra_env: Optional[dict[str, str]] = None,
) -> tuple[list[str], Optional[str]]:
    """Build the (argv, subprocess_cwd) for a run. Pure — unit-tested per platform.

    Native: argv runs claude directly in ``cwd``. WSL: argv is
    ``wsl -d <distro> --cd <wslcwd> -- [env -u API_KEY…] <claude> …`` with file
    paths translated; the subprocess cwd is ``None`` (WSL ``--cd`` sets the dir).
    ``strip_api_key`` drops API-key env vars so the CLI uses the subscription.
    ``wsl_extra_env`` is folded into the in-WSL ``env`` prefix (native runs pass
    extra env via the subprocess env instead — see :func:`_clean_env`).
    """
    if runtime.kind == "wsl":
        mcp = to_wsl_path(mcp_config) if mcp_config else None
        flags = _flags(model=model, mcp_config=mcp, allowed_tools=allowed_tools,
                       append_system_prompt=append_system_prompt,
                       permission_mode=permission_mode, strict_mcp=strict_mcp)
        prefix = _wsl_env_prefix(strip_api_key, wsl_extra_env)
        argv = ["wsl", "-d", runtime.distro or "Ubuntu", "--cd", to_wsl_path(cwd),
                "--", *prefix, runtime.exe, "-p", prompt, *flags]
        return argv, None
    flags = _flags(model=model, mcp_config=(str(mcp_config) if mcp_config else None),
                   allowed_tools=allowed_tools, append_system_prompt=append_system_prompt,
                   permission_mode=permission_mode, strict_mcp=strict_mcp)
    return [runtime.exe, "-p", prompt, *flags], str(cwd)


def _flags_primed(
    *, model: Optional[str], mcp_config: Optional[str], allowed_tools: Optional[list[str]],
    append_system_prompt: Optional[str], permission_mode: str, strict_mcp: bool,
) -> list[str]:
    """Flags for the primed (stream-json) path: same as :func:`_flags` but using
    stream-json I/O so a priming turn can precede the real prompt in one session."""
    flags = [
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--permission-mode", permission_mode,
    ]
    if model:
        flags += ["--model", model]
    if mcp_config:
        flags += ["--mcp-config", mcp_config]
        if strict_mcp:
            flags += ["--strict-mcp-config"]
    if allowed_tools:
        flags += ["--allowedTools", ",".join(allowed_tools)]
    if append_system_prompt:
        flags += ["--append-system-prompt", append_system_prompt]
    return flags


def build_argv_primed(
    runtime: ClaudeRuntime, *, cwd: str | Path,
    model: Optional[str] = None, mcp_config: Optional[str | Path] = None,
    allowed_tools: Optional[list[str]] = None, append_system_prompt: Optional[str] = None,
    permission_mode: str = "bypassPermissions", strict_mcp: bool = False,
    strip_api_key: bool = True, wsl_extra_env: Optional[dict[str, str]] = None,
) -> tuple[list[str], Optional[str]]:
    """(argv, cwd) for a primed run. The prompt is fed via stdin (stream-json), so
    unlike :func:`build_argv` no ``-p <prompt>`` positional is included."""
    if runtime.kind == "wsl":
        mcp = to_wsl_path(mcp_config) if mcp_config else None
        flags = _flags_primed(model=model, mcp_config=mcp, allowed_tools=allowed_tools,
                              append_system_prompt=append_system_prompt,
                              permission_mode=permission_mode, strict_mcp=strict_mcp)
        prefix = _wsl_env_prefix(strip_api_key, wsl_extra_env)
        argv = ["wsl", "-d", runtime.distro or "Ubuntu", "--cd", to_wsl_path(cwd),
                "--", *prefix, runtime.exe, "-p", *flags]
        return argv, None
    flags = _flags_primed(model=model, mcp_config=(str(mcp_config) if mcp_config else None),
                          allowed_tools=allowed_tools, append_system_prompt=append_system_prompt,
                          permission_mode=permission_mode, strict_mcp=strict_mcp)
    return [runtime.exe, "-p", *flags], str(cwd)


def run_claude_primed(
    prompt: str, *, cwd: str | Path, model: Optional[str] = None,
    mcp_config: Optional[str | Path] = None, allowed_tools: Optional[list[str]] = None,
    append_system_prompt: Optional[str] = None, permission_mode: str = "bypassPermissions",
    strict_mcp: bool = False, strip_api_key: bool = True, timeout: int = 300,
    runtime: Optional[ClaudeRuntime] = None, extra_env: Optional[dict[str, str]] = None,
) -> ClaudeResult:
    """Run one headless turn with a *priming turn* first, over stream-json I/O.

    Sends two user messages in a single session: a trivial priming message, then
    the real ``prompt``. The priming turn gives Claude Code's async MCP connection
    a full turn to finish registering tools before the model generates the real
    answer — eliminating the startup race that drops ``memory_recall`` on ~half of
    plain ``claude -p`` invocations. Returns the LAST result event (the real
    answer). Used by the plugin (MCP) path; the plain text path is unchanged.
    ``extra_env`` (e.g. ``PATH`` / ``CLAUDE_PROJECT_DIR``) is added to the CLI's
    environment so an installed plugin's MCP-server command and store path resolve.
    """
    rt = require_runtime(runtime)
    argv, sub_cwd = build_argv_primed(
        rt, cwd=cwd, model=model, mcp_config=mcp_config, allowed_tools=allowed_tools,
        append_system_prompt=append_system_prompt, permission_mode=permission_mode,
        strict_mcp=strict_mcp, strip_api_key=strip_api_key, wsl_extra_env=extra_env,
    )
    stdin_data = _stream_json_input([_PRIME_MESSAGE, prompt])
    for attempt in range(3):
        env = _clean_env(strip_api_key, extra_env)
        with _ClaudeProgressMonitor(cwd=cwd, env=env, attempt=attempt + 1):
            proc = subprocess.run(argv, cwd=sub_cwd, capture_output=True, text=True,
                                  timeout=timeout, env=env, input=stdin_data)
        if proc.returncode == 0:
            return _parse_stream_json(proc.stdout)
        diag = _diagnose_primed_failure(proc.stdout, proc.stderr)
        # Retry transient startup failures, NOT a model/tool error. Two known
        # transients: (1) the .mcp.json read miss on the WSL DrvFs mount; (2) a
        # non-zero exit during session STARTUP whose stdout's first stream-json line
        # is a `hook_started` event (the SessionStart hook firing) with no result
        # event ever emitted — claude died mid-startup before the real turn. Both
        # are state/timing dependent (a re-run of the SAME task succeeds), so a
        # bounded retry of the whole primed turn clears them without masking a real
        # model error (which DOES emit a result event -> not retried here).
        if attempt < 2 and (
            diag.is_mcp_config_miss
            or diag.is_startup_abort
            or diag.is_connection_closed_mid_response
        ):
            continue
        raise RuntimeError(f"claude (primed) exited {proc.returncode}: {diag.message}")
    return _parse_stream_json(proc.stdout)


@dataclass(slots=True)
class _TranscriptStats:
    lines: int = 0
    assistant_events: int = 0
    tool_uses: int = 0
    tool_results: int = 0
    api_errors: int = 0
    last_type: str = ""
    last_mtime: float = 0.0


def _progress_interval_secs() -> float:
    raw = os.environ.get(_PROGRESS_ENV)
    if raw is None:
        return _PROGRESS_DEFAULT_SECS
    if raw.strip().lower() in {"0", "false", "no", "off"}:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _PROGRESS_DEFAULT_SECS


def _project_transcript_root(env: Optional[dict[str, str]]) -> Optional[Path]:
    cfg = (env or {}).get("CLAUDE_CONFIG_DIR") or sandbox.active_config_dir()
    return (Path(cfg) / "projects") if cfg else None


def _latest_transcript(projects: Optional[Path], *, since: float) -> Optional[Path]:
    if projects is None or not projects.is_dir():
        return None
    candidates: list[Path] = []
    for p in projects.glob("**/*.jsonl"):
        try:
            if p.stat().st_mtime >= since:
                candidates.append(p)
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _summarize_transcript(path: Path) -> _TranscriptStats:
    stats = _TranscriptStats()
    try:
        stats.last_mtime = path.stat().st_mtime
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return stats
    stats.lines = len(lines)
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        typ = str(ev.get("type") or "")
        if typ:
            stats.last_type = typ
        if typ == "assistant":
            stats.assistant_events += 1
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            for item in msg.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    stats.tool_uses += 1
        elif typ == "user":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            for item in msg.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    stats.tool_results += 1
        if ev.get("isApiErrorMessage") or ev.get("error"):
            stats.api_errors += 1
    return stats


class _ClaudeProgressMonitor:
    """Periodic, best-effort transcript heartbeat for long blocking Claude runs."""

    def __init__(self, *, cwd: str | Path, env: Optional[dict[str, str]],
                 attempt: int) -> None:
        self.cwd = Path(cwd)
        self.env = env
        self.attempt = attempt
        self.interval = _progress_interval_secs()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "_ClaudeProgressMonitor":
        if self.interval <= 0:
            return self
        self._thread = threading.Thread(target=self._run, name="claude-progress",
                                        daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        started = time.time()
        projects = _project_transcript_root(self.env)
        last_printed: tuple[Optional[Path], int, int, int, int] = (None, -1, -1, -1, -1)
        while not self._stop.wait(self.interval):
            transcript = _latest_transcript(projects, since=started - 5.0)
            elapsed = int(time.time() - started)
            if transcript is None:
                print(f"  claude still running · attempt {self.attempt} · {elapsed}s · "
                      "waiting for transcript", file=sys.stderr, flush=True)
                continue
            stats = _summarize_transcript(transcript)
            key = (transcript, stats.lines, stats.tool_uses, stats.tool_results,
                   stats.api_errors)
            if key == last_printed:
                print(f"  claude still running · attempt {self.attempt} · {elapsed}s · "
                      f"no new transcript events · {transcript.name}",
                      file=sys.stderr, flush=True)
            else:
                print(f"  claude still running · attempt {self.attempt} · {elapsed}s · "
                      f"lines={stats.lines} assistant={stats.assistant_events} "
                      f"tool_uses={stats.tool_uses} tool_results={stats.tool_results} "
                      f"api_errors={stats.api_errors} last={stats.last_type or '?'} · "
                      f"{transcript.name}",
                      file=sys.stderr, flush=True)
                last_printed = key


@dataclass(slots=True)
class _PrimedFailure:
    """Classification + a *useful* error message for a non-zero primed exit.

    The naive ``(proc.stderr or proc.stdout)[:400]`` discards the real reason in two
    common ways: (1) when stdout is non-empty, stderr — where claude writes its crash
    traceback — is never looked at; (2) the truncation keeps only the FIRST stream-json
    line, which on a startup abort is the ``SessionStart`` ``hook_started`` system
    event, making every such failure *look* like a hook bug when the hook returns 0 and
    the abort is elsewhere. ``message`` therefore folds in BOTH streams and prefers the
    tail (where the failure surfaces) over the head."""

    message: str
    is_mcp_config_miss: bool
    is_startup_abort: bool
    is_connection_closed_mid_response: bool


def _diagnose_primed_failure(stdout: Optional[str], stderr: Optional[str]) -> _PrimedFailure:
    """Build a :class:`_PrimedFailure` from a non-zero primed run's output streams."""
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    combined = "\n".join(p for p in (out, err) if p)
    is_mcp = "MCP config" in combined
    is_closed_mid_response = "Connection closed mid-response" in combined
    # A startup abort: stream-json began (a `hook_started`/`system` event was emitted)
    # but NO `result` event ever landed — claude exited during startup, before the real
    # turn produced an answer. A genuine model/tool error emits a result event with
    # `is_error`, which we do NOT treat as a transient startup abort.
    saw_result = '"type":"result"' in combined or '"type": "result"' in combined
    saw_startup = (
        '"subtype":"hook_started"' in combined
        or '"subtype": "hook_started"' in combined
        or '"hook_event":"SessionStart"' in combined
        or '"hook_event": "SessionStart"' in combined
        or '"type":"system"' in combined
        or '"type": "system"' in combined
    )
    is_startup_abort = saw_startup and not saw_result
    # Prefer stderr (the crash reason) then the TAIL of stdout (the last events before
    # exit), not just the first 400 chars of stdout (which is the misleading hook event).
    if err:
        message = err[-400:]
    elif out:
        message = out[-400:]
    else:
        message = "(no output on stdout or stderr)"
    return _PrimedFailure(
        message=message,
        is_mcp_config_miss=is_mcp,
        is_startup_abort=is_startup_abort,
        is_connection_closed_mid_response=is_closed_mid_response,
    )


def _wsl_env_prefix(strip_api_key: bool,
                    extra_env: Optional[dict[str, str]] = None) -> list[str]:
    """The ``env ...`` prefix run *inside* WSL before ``claude``.

    Env vars don't cross the Windows->WSL boundary, so the native ``_clean_env``
    can't reach the in-WSL CLI. We instead express the same adjustments as an
    ``env`` command in the WSL argv: ``-u VAR`` unsets each API key,
    ``CLAUDE_CONFIG_DIR=<wsl-path>`` points the in-WSL CLI at the sandbox (path
    translated to its ``/mnt`` form), and any ``extra_env`` is appended verbatim
    (the caller supplies WSL-correct values). Returns ``[]`` when nothing is set."""
    parts: list[str] = []
    if strip_api_key:
        for v in _API_KEY_VARS:
            parts += ["-u", v]
    sandbox_dir = sandbox.active_config_dir()
    if sandbox_dir is not None:
        parts.append(f"CLAUDE_CONFIG_DIR={to_wsl_path(sandbox_dir)}")
    for key, val in (extra_env or {}).items():
        parts.append(f"{key}={val}")
    return ["env", *parts] if parts else []


def _clean_env(strip_api_key: bool,
               extra_env: Optional[dict[str, str]] = None) -> Optional[dict]:
    """Subprocess env for the ``claude`` CLI.

    Adjustments to the inherited environment:

    * API-key vars removed (so WSLENV can't forward them and a native CLI can't
      read them) when ``strip_api_key`` — the CLI then uses the subscription.
    * ``CLAUDE_CONFIG_DIR`` set to the sandbox dir when one is active
      (:func:`memeval.claudecode.sandbox.active_config_dir`), so the CLI reads
      only the seeded sandbox config — no host skills / agents / ``CLAUDE.md`` —
      instead of ``~/.claude``.
    * ``extra_env`` merged on top (e.g. ``PATH`` so a plugin's MCP-server command
      resolves, ``CLAUDE_PROJECT_DIR`` so its store path resolves).

    Returns ``None`` (keep the inherited env unchanged) only when no adjustment
    applies."""
    sandbox_dir = sandbox.active_config_dir()
    if not strip_api_key and sandbox_dir is None and not extra_env:
        return None
    env = {k: v for k, v in os.environ.items()
           if not (strip_api_key and k in _API_KEY_VARS)}
    if sandbox_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = sandbox_dir
    if extra_env:
        env.update(extra_env)
    return env


def run_claude(
    prompt: str, *, cwd: str | Path, model: Optional[str] = None,
    mcp_config: Optional[str | Path] = None, allowed_tools: Optional[list[str]] = None,
    append_system_prompt: Optional[str] = None, permission_mode: str = "bypassPermissions",
    strict_mcp: bool = False, strip_api_key: bool = True, timeout: int = 300,
    runtime: Optional[ClaudeRuntime] = None, extra_env: Optional[dict[str, str]] = None,
) -> ClaudeResult:
    """Run one headless ``claude -p`` turn (native or WSL) and return text + usage.

    ``strip_api_key`` (default True) makes the CLI authenticate with the Claude
    Code subscription, never an API key — no API billing for benchmark runs.
    ``extra_env`` is added to the CLI's environment (e.g. ``PATH`` /
    ``CLAUDE_PROJECT_DIR`` for an installed plugin's MCP server + store path).
    """
    rt = require_runtime(runtime)
    argv, sub_cwd = build_argv(
        rt, prompt, cwd=cwd, model=model, mcp_config=mcp_config, allowed_tools=allowed_tools,
        append_system_prompt=append_system_prompt, permission_mode=permission_mode,
        strict_mcp=strict_mcp, strip_api_key=strip_api_key, wsl_extra_env=extra_env,
    )
    # stdin=DEVNULL: headless `claude -p` reads its prompt from argv, but without a
    # TTY (e.g. a background process) it waits 3s for stdin and prints a warning
    # that can mask the real error — close stdin so it proceeds immediately.
    # Retry transient MCP-config read failures: under WSL the .mcp.json lives on the
    # /mnt/c DrvFs mount, which intermittently fails the stat/read ("MCP config file
    # not found") before any model turn — so a retry is cheap and usually succeeds.
    for attempt in range(3):
        proc = subprocess.run(argv, cwd=sub_cwd, capture_output=True, text=True,
                              timeout=timeout, env=_clean_env(strip_api_key, extra_env),
                              stdin=subprocess.DEVNULL)
        if proc.returncode == 0:
            return _parse(proc.stdout)
        err = (proc.stderr or proc.stdout or "").strip()
        if "MCP config" in err and attempt < 2:
            continue  # transient DrvFs read miss — retry
        raise RuntimeError(f"claude exited {proc.returncode}: {err[:400]}")
    return _parse(proc.stdout)  # unreachable, keeps type-checkers happy


#: Minimal priming turn sent before the real prompt on the plugin (MCP) path. Its
#: only job is to give Claude Code's *async* MCP connection a full turn to finish
#: registering tools before the model generates the answer to the real question —
#: closing the startup race where ``claude -p`` begins generating before
#: ``memory_recall`` is available (~40-65% first-try without it, ~100% with it).
_PRIME_MESSAGE = (
    "Reply with the single word READY. This is an internal setup turn — "
    "do not call any tools and do not answer anything else yet."
)


def _stream_json_input(messages: list[str]) -> str:
    """Serialize user turns as newline-delimited stream-json input for ``claude -p``."""
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": m}})
        for m in messages
    ]
    return "\n".join(lines) + "\n"


def _parse_stream_json(stdout: str) -> ClaudeResult:
    """Parse ``--output-format stream-json`` (one JSON object per line).

    Returns the LAST ``result`` event — with a priming turn first, that is the
    answer to the real prompt, not the priming reply. Usage/cost are summed across
    result events so multi-turn token accounting stays correct.
    """
    last: dict[str, Any] = {}
    tin = tout = turns = 0
    cost = 0.0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "result":
            continue
        last = ev
        usage = ev.get("usage") or {}
        tin += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
        tout += int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        cost += float(ev.get("total_cost_usd", ev.get("cost_usd", 0.0)) or 0.0)
        turns += int(ev.get("num_turns", 0) or 0)
    text = ""
    for key in ("result", "text", "response", "content"):
        v = last.get(key)
        if isinstance(v, str) and v:
            text = v
            break
    return ClaudeResult(text=text, tokens_in=tin, tokens_out=tout,
                        cost_usd=cost, num_turns=turns, raw=last)


def _parse(stdout: str) -> ClaudeResult:
    """Parse the ``--output-format json`` envelope; tolerant of schema variation."""
    data: dict[str, Any] = {}
    s = (stdout or "").strip()
    try:
        data = json.loads(s)
    except Exception:
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
    text = ""
    for key in ("result", "text", "response", "content"):
        v = data.get(key)
        if isinstance(v, str) and v:
            text = v
            break
    usage = data.get("usage") or {}
    tin = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    tout = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    cost = float(data.get("total_cost_usd", data.get("cost_usd", 0.0)) or 0.0)
    turns = int(data.get("num_turns", 0) or 0)
    return ClaudeResult(text=text, tokens_in=tin, tokens_out=tout, cost_usd=cost, num_turns=turns, raw=data)


__all__ = ["ClaudeResult", "ClaudeNotInstalled", "find_claude", "require_runtime",
           "build_argv", "run_claude", "build_argv_primed", "run_claude_primed"]
