"""Native OS folder-picker for the inspector's "Browse…" button.

The inspector is a localhost-only server running on the user's own machine, so it can pop a
**native** directory dialog server-side and hand the real absolute path back to the page — a
browser ``<input type=file>`` can't do this (it sandboxes to a fake path). ``POST /api/pick-store``
calls :func:`pick_directory`; the page then feeds the chosen dir to the existing ``/api/reopen``.

Strategy (no third-party deps):
  * **macOS** — ``osascript`` ``choose folder`` (the real Finder dialog; returns a POSIX path).
  * **elsewhere** — ``tkinter.filedialog.askdirectory`` in a *subprocess*, so a GUI that fails
    to import or a dialog the user leaves open can never wedge a server thread.

Either way the dialog runs in a short-lived subprocess under a timeout. A cancelled dialog (or an
unavailable GUI) yields ``None`` — the caller reports "cancelled", not an error.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Generous: the user may take a while to navigate the dialog. The subprocess is the only thing
# blocked — server threads are not — so this just bounds a truly stuck/abandoned dialog.
_TIMEOUT_S = 300

# Run tkinter's askdirectory in a child interpreter and print the chosen path (empty = cancel).
# Kept as a module-run string so no temp file is needed; stdout is the channel, stderr is ignored.
_TK_SNIPPET = (
    "import tkinter, tkinter.filedialog as fd;"
    "r=tkinter.Tk();r.withdraw();r.attributes('-topmost',True);"
    "p=fd.askdirectory(title='Choose a memory store (a .../_memory directory)');"
    "print(p or '')"
)


class PickerUnavailable(RuntimeError):
    """No native folder dialog is available on this platform/environment."""


def pick_directory(initial: str | None = None) -> str | None:
    """Open a native folder-picker and return the chosen absolute path, or ``None`` if the user
    cancelled. Raises :class:`PickerUnavailable` if no dialog mechanism works here (e.g. a
    headless box with no ``osascript`` and no Tk).

    ``initial`` seeds the dialog's starting directory when the platform supports it (macOS).
    """
    if sys.platform == "darwin":
        return _pick_macos(initial)
    return _pick_tk()


def _pick_macos(initial: str | None) -> str | None:
    prompt = _osa_str("Choose a memory store (a .../_memory directory)")
    default = ""
    if initial:
        start = Path(initial)
        # `choose folder` wants an existing location; fall back to the parent of a file/missing dir.
        if not start.is_dir():
            start = start.parent
        if start.is_dir():
            default = f" default location (POSIX file {_osa_str(str(start))})"
    script = f"POSIX path of (choose folder with prompt {prompt}{default})"
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except FileNotFoundError as exc:  # no osascript (non-standard macOS)
        raise PickerUnavailable("osascript not found") from exc
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        # user pressed Cancel -> AppleScript "User canceled" (code 1); treat as cancel, not error.
        if "User canceled" in (proc.stderr or "") or proc.returncode == 1:
            return None
        raise PickerUnavailable((proc.stderr or "osascript failed").strip())
    path = proc.stdout.strip()
    return path or None


def _pick_tk() -> str | None:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _TK_SNIPPET],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        raise PickerUnavailable(
            "no native folder dialog available (tkinter could not start: "
            f"{(proc.stderr or '').strip().splitlines()[-1] if proc.stderr else 'unknown'})"
        )
    path = proc.stdout.strip()
    return path or None


def _osa_str(s: str) -> str:
    """Quote a Python string as an AppleScript string literal (escape backslash + double-quote)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


__all__ = ["pick_directory", "PickerUnavailable"]
