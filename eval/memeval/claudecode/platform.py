"""Cross-platform discovery of the Claude Code CLI: macOS, Linux, Windows, and
Windows→WSL.

Three launch strategies:

* **native** — ``claude`` is on this OS directly (macOS / Linux, or Windows with
  the CLI installed natively). Run it as a normal subprocess.
* **wsl** — we're on Windows and ``claude`` lives inside a WSL distro. Run it via
  ``wsl -d <distro> --cd <path> -- <claude> …`` with Windows paths translated to
  ``/mnt/<drive>/…``.

Overrides (env): ``CLAUDE_CLI`` (native path), ``CLAUDE_WSL_DISTRO`` (default
``Ubuntu``), ``CLAUDE_WSL_PYTHON`` (the python that has ``memeval``+``mcp`` inside
WSL, used by the memory plugin; default ``python3``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


@dataclass(slots=True)
class ClaudeRuntime:
    """How to launch the Claude Code CLI on this machine."""
    kind: str                    # "native" | "wsl"
    exe: str                     # path/name of the claude binary
    distro: Optional[str] = None # WSL distro (kind == "wsl")
    python: str = "python"       # python with memeval(+mcp) in claude's environment


def to_wsl_path(p: "str | Path") -> str:
    """Translate a Windows path to its WSL ``/mnt`` form (idempotent for POSIX).

    A relative Windows path (e.g. ``..\\runs\\out``) has no drive to map, so it is
    first resolved to an absolute path against the current directory — otherwise
    ``wsl --cd <relative>`` fails with ``E_INVALIDARG``. POSIX paths (already
    ``/mnt/...`` or ``/home/...``) pass through unchanged.
    """
    s = str(p)
    if s.startswith("/"):              # already a POSIX/WSL path
        return s.replace("\\", "/")
    m = _WIN_DRIVE.match(s)
    if not m:                          # relative (drive-less) -> make absolute first
        s = str(Path(s).resolve())
        m = _WIN_DRIVE.match(s)
    if m:
        drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return s.replace("\\", "/")


def _on_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def find_native() -> Optional[str]:
    """Path to a natively-installed ``claude`` (env override, PATH, npm-global)."""
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


def _wsl_distro() -> str:
    return os.environ.get("CLAUDE_WSL_DISTRO") or "Ubuntu"


def find_in_wsl(distro: Optional[str] = None) -> Optional[str]:
    """Return the in-WSL path to ``claude`` (via a login shell), or ``None``."""
    if not _on_windows() or not shutil.which("wsl"):
        return None
    distro = distro or _wsl_distro()
    try:
        out = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lic", "command -v claude"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    path = (out.stdout or "").strip().splitlines()
    path = [p for p in path if p.startswith("/")]
    return path[-1] if path else None


def detect() -> Optional[ClaudeRuntime]:
    """Pick the launch strategy for this machine (native first, then WSL)."""
    native = find_native()
    if native:
        return ClaudeRuntime(kind="native", exe=native, python=sys.executable or "python")
    if _on_windows():
        distro = _wsl_distro()
        wsl_exe = find_in_wsl(distro)
        if wsl_exe:
            return ClaudeRuntime(
                kind="wsl", exe=wsl_exe, distro=distro,
                python=os.environ.get("CLAUDE_WSL_PYTHON") or "python3",
            )
    return None


def describe() -> str:
    """One-line human description of what was detected (for diagnostics)."""
    rt = detect()
    if rt is None:
        return "claude CLI: NOT FOUND (native or WSL)"
    if rt.kind == "wsl":
        return f"claude CLI: WSL[{rt.distro}] {rt.exe} (python={rt.python})"
    return f"claude CLI: native {rt.exe}"


__all__ = ["ClaudeRuntime", "to_wsl_path", "find_native", "find_in_wsl", "detect", "describe"]
