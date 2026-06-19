"""Locate and drive the **Claude Code CLI** headlessly.

``run_claude`` runs ``claude -p <prompt> --output-format json`` in a given
working directory (optionally with an MCP config + allowed tools), parses the
JSON envelope, and returns the answer text plus token usage. No third-party deps;
shells out to the user-installed ``claude`` binary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class ClaudeNotInstalled(RuntimeError):
    """Raised when the ``claude`` CLI can't be found on the system."""


@dataclass(slots=True)
class ClaudeResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def find_claude() -> Optional[str]:
    """Return the path to the ``claude`` CLI, or ``None`` if not installed.

    Checks ``$CLAUDE_CLI``, then ``PATH`` (claude / claude.cmd / claude.exe),
    then the common npm-global location on Windows.
    """
    env = os.environ.get("CLAUDE_CLI")
    if env and Path(env).exists():
        return env
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    appdata = os.environ.get("APPDATA")
    if appdata:
        for name in ("claude.cmd", "claude"):
            cand = Path(appdata) / "npm" / name
            if cand.exists():
                return str(cand)
    return None


def require_claude() -> str:
    """Return the ``claude`` path or raise a clear install message."""
    path = find_claude()
    if not path:
        raise ClaudeNotInstalled(
            "The Claude Code CLI was not found. Install it with "
            "`npm install -g @anthropic-ai/claude-code` (or set $CLAUDE_CLI to its "
            "path), then re-run. The CLI is what executes each benchmark task."
        )
    return path


def run_claude(
    prompt: str,
    *,
    cwd: str | Path,
    model: Optional[str] = None,
    mcp_config: Optional[str | Path] = None,
    allowed_tools: Optional[list[str]] = None,
    append_system_prompt: Optional[str] = None,
    permission_mode: str = "bypassPermissions",
    timeout: int = 300,
    claude_path: Optional[str] = None,
) -> ClaudeResult:
    """Run one headless ``claude -p`` turn in ``cwd`` and return text + usage.

    ``mcp_config`` points the run at an MCP server (our memory plugin);
    ``allowed_tools`` whitelists the tools the agent may call (e.g. the memory
    tools) so a headless run isn't blocked on permissions.
    """
    exe = claude_path or require_claude()
    cmd = [exe, "-p", prompt, "--output-format", "json", "--permission-mode", permission_mode]
    if model:
        cmd += ["--model", model]
    if mcp_config:
        cmd += ["--mcp-config", str(mcp_config)]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if append_system_prompt:
        cmd += ["--append-system-prompt", append_system_prompt]

    proc = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    return _parse(proc.stdout)


def _parse(stdout: str) -> ClaudeResult:
    """Parse the ``--output-format json`` envelope into a ClaudeResult.

    Tolerates minor schema variation across CLI versions: pulls the result text
    from ``result``/``text``, and usage from ``usage`` (input_tokens/output_tokens).
    """
    data: dict[str, Any] = {}
    s = (stdout or "").strip()
    try:
        data = json.loads(s)
    except Exception:
        # Some versions stream NDJSON; take the last JSON object line.
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


__all__ = ["ClaudeResult", "ClaudeNotInstalled", "find_claude", "require_claude", "run_claude"]
