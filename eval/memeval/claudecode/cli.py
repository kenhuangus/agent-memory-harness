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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .platform import ClaudeRuntime, detect, to_wsl_path

#: Credentials stripped so the CLI uses the Claude Code *subscription* (OAuth),
#: never an API key — benchmarking Claude Code on its own auth, no API billing.
_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


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
    strip_api_key: bool = True,
) -> tuple[list[str], Optional[str]]:
    """Build the (argv, subprocess_cwd) for a run. Pure — unit-tested per platform.

    Native: argv runs claude directly in ``cwd``. WSL: argv is
    ``wsl -d <distro> --cd <wslcwd> -- [env -u API_KEY…] <claude> …`` with file
    paths translated; the subprocess cwd is ``None`` (WSL ``--cd`` sets the dir).
    ``strip_api_key`` drops API-key env vars so the CLI uses the subscription.
    """
    if runtime.kind == "wsl":
        mcp = to_wsl_path(mcp_config) if mcp_config else None
        flags = _flags(model=model, mcp_config=mcp, allowed_tools=allowed_tools,
                       append_system_prompt=append_system_prompt,
                       permission_mode=permission_mode, strict_mcp=strict_mcp)
        prefix: list[str] = []
        if strip_api_key:
            prefix = ["env"] + [a for v in _API_KEY_VARS for a in ("-u", v)]
        argv = ["wsl", "-d", runtime.distro or "Ubuntu", "--cd", to_wsl_path(cwd),
                "--", *prefix, runtime.exe, "-p", prompt, *flags]
        return argv, None
    flags = _flags(model=model, mcp_config=(str(mcp_config) if mcp_config else None),
                   allowed_tools=allowed_tools, append_system_prompt=append_system_prompt,
                   permission_mode=permission_mode, strict_mcp=strict_mcp)
    return [runtime.exe, "-p", prompt, *flags], str(cwd)


def _clean_env(strip_api_key: bool) -> Optional[dict]:
    """Subprocess env with API-key vars removed (so WSLENV can't forward them and
    a native CLI can't read them). ``None`` keeps the inherited env."""
    if not strip_api_key:
        return None
    return {k: v for k, v in os.environ.items() if k not in _API_KEY_VARS}


def run_claude(
    prompt: str, *, cwd: str | Path, model: Optional[str] = None,
    mcp_config: Optional[str | Path] = None, allowed_tools: Optional[list[str]] = None,
    append_system_prompt: Optional[str] = None, permission_mode: str = "bypassPermissions",
    strict_mcp: bool = False, strip_api_key: bool = True, timeout: int = 300,
    runtime: Optional[ClaudeRuntime] = None,
) -> ClaudeResult:
    """Run one headless ``claude -p`` turn (native or WSL) and return text + usage.

    ``strip_api_key`` (default True) makes the CLI authenticate with the Claude
    Code subscription, never an API key — no API billing for benchmark runs.
    """
    rt = require_runtime(runtime)
    argv, sub_cwd = build_argv(
        rt, prompt, cwd=cwd, model=model, mcp_config=mcp_config, allowed_tools=allowed_tools,
        append_system_prompt=append_system_prompt, permission_mode=permission_mode,
        strict_mcp=strict_mcp, strip_api_key=strip_api_key,
    )
    # stdin=DEVNULL: headless `claude -p` reads its prompt from argv, but without a
    # TTY (e.g. a background process) it waits 3s for stdin and prints a warning
    # that can mask the real error — close stdin so it proceeds immediately.
    # Retry transient MCP-config read failures: under WSL the .mcp.json lives on the
    # /mnt/c DrvFs mount, which intermittently fails the stat/read ("MCP config file
    # not found") before any model turn — so a retry is cheap and usually succeeds.
    for attempt in range(3):
        proc = subprocess.run(argv, cwd=sub_cwd, capture_output=True, text=True,
                              timeout=timeout, env=_clean_env(strip_api_key),
                              stdin=subprocess.DEVNULL)
        if proc.returncode == 0:
            return _parse(proc.stdout)
        err = (proc.stderr or proc.stdout or "").strip()
        if "MCP config" in err and attempt < 2:
            continue  # transient DrvFs read miss — retry
        raise RuntimeError(f"claude exited {proc.returncode}: {err[:400]}")
    return _parse(proc.stdout)  # unreachable, keeps type-checkers happy


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
           "build_argv", "run_claude"]
