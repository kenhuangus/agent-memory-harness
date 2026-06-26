"""Discover the **Cursor CLI** (`cursor-agent`) on this machine.

Far simpler than the Claude Code platform module: Cursor ships a single native
binary (``cursor-agent``) installed via its curl script to
``~/.local/bin/cursor-agent`` (a symlink into ``~/.local/share/cursor-agent/...``),
and there is no WSL split to handle for the eval use case. We just resolve a usable
executable, honoring an override env var.

Overrides (env): ``CURSOR_AGENT_CLI`` (or ``CURSOR_CLI``) — an explicit path to the
``cursor-agent`` binary.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class CursorRuntime:
    """How to launch the Cursor Agent CLI on this machine."""

    exe: str                       # path/name of the cursor-agent binary
    python: str = sys.executable or "python"  # python that can import the plugin engine


_OVERRIDE_VARS = ("CURSOR_AGENT_CLI", "CURSOR_CLI")
# Names the install script may produce, in resolution order.
_CANDIDATES = ("cursor-agent", "cursor-agent.cmd", "cursor-agent.exe")
# The default install location of the curl-installed binary, in case it is not on PATH.
_DEFAULT_BIN = Path.home() / ".local" / "bin" / "cursor-agent"


def detect() -> Optional[CursorRuntime]:
    """Return a :class:`CursorRuntime` for a usable ``cursor-agent``, or ``None``.

    Resolution order: an explicit override env var; then PATH; then the curl
    installer's default ``~/.local/bin/cursor-agent``. The interpreter recorded as
    ``python`` is the harness's own by default — the plugin-engine interpreter is
    resolved separately at MCP-wiring time (it lives in the plugin's venv)."""
    for var in _OVERRIDE_VARS:
        override = (os.environ.get(var) or "").strip()
        if override and Path(override).exists():
            return CursorRuntime(exe=override)
    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return CursorRuntime(exe=found)
    if _DEFAULT_BIN.exists():
        return CursorRuntime(exe=str(_DEFAULT_BIN))
    return None


def describe(rt: Optional[CursorRuntime]) -> str:
    """Human-readable one-liner for logs."""
    if rt is None:
        return "cursor-agent CLI: NOT FOUND"
    return f"cursor-agent CLI: {rt.exe}"


__all__ = ["CursorRuntime", "detect", "describe"]
