"""A sandboxed ``CLAUDE_CONFIG_DIR`` for benchmark runs.

A normal ``claude`` invocation reads the *host* user's ``~/.claude`` — global
``CLAUDE.md``, every installed skill and agent, ``settings.json``, MCP servers.
For benchmarking the memory harness we want the opposite: a ``claude`` that sees
*only* what we explicitly hand it (the memory plugin via ``--mcp-config``) and
nothing of the host. The CLI honours ``CLAUDE_CONFIG_DIR`` — point it at a fresh
directory and the CLI keeps all its state (``.claude.json``, ``projects/``,
``sessions/``) there instead of ``~/.claude``, and discovers no host skills /
agents / global ``CLAUDE.md``.

The one thing a fresh config dir lacks is auth: it is logged out, and that is
**by design** — auth is *not* seeded from the host. We investigated copying
``~/.claude/.credentials.json`` across and it does not work: the live token is
held in the OS keychain (macOS) bound to the default config, the on-disk file is
a stale leftover, and headless ``claude -p`` does not refresh an expired token
(it sends it as-is and gets a 401). Copying the live keychain secret into a
plaintext file in the sandbox would be the only way to seed it — a credential-
handling step we deliberately avoid. Instead the sandbox is authenticated **once,
interactively**, and keeps its own token thereafter::

    CLAUDE_CONFIG_DIR=<sandbox> claude     # then run /login

That mints the sandbox its own credential under its own config dir; because the
dir is gitignored it never leaves the machine.

Resolution order for the active sandbox (``active_config_dir``):

* ``MEMEVAL_SANDBOX_CONFIG_DIR`` set and non-empty  -> use it verbatim.
* ``MEMEVAL_SANDBOX`` set to a falsey value (``0``/``false``/``no``/``off``)
  -> disabled, return ``None`` (the CLI uses the host ``~/.claude``).
* otherwise -> the default project sandbox (:func:`default_config_dir`) **iff it
  already exists**; if it has not been built, return ``None`` so behaviour is
  unchanged until someone opts in by running :func:`build`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

#: Env var: explicit path to a config dir to sandbox into (highest precedence).
ENV_CONFIG_DIR = "MEMEVAL_SANDBOX_CONFIG_DIR"
#: Env var: set to a falsey value to force-disable sandboxing.
ENV_TOGGLE = "MEMEVAL_SANDBOX"

_FALSEY = {"0", "false", "no", "off", ""}


def default_config_dir() -> Path:
    """The project-local sandbox dir: ``eval/.claude-sandbox`` (gitignored).

    Anchored to the package so it is stable regardless of the process cwd
    (``run_bench`` runs ``claude`` with varying working directories).
    """
    # this file: eval/memeval/claudecode/sandbox.py -> parents[2] == eval/
    return Path(__file__).resolve().parents[2] / ".claude-sandbox"


def active_config_dir() -> Optional[str]:
    """Resolve the config dir to sandbox into, or ``None`` to use the host.

    Pure (reads env + filesystem, no writes). See module docstring for the
    resolution order. Returns a string path (what the CLI env wants) or ``None``.
    """
    explicit = os.environ.get(ENV_CONFIG_DIR)
    if explicit:
        return explicit
    toggle = os.environ.get(ENV_TOGGLE)
    if toggle is not None and toggle.strip().lower() in _FALSEY:
        return None
    default = default_config_dir()
    return str(default) if exists(default) else None


def exists(config_dir: Optional[Path] = None) -> bool:
    """True if the sandbox dir has been built (its ``settings.json`` is present)."""
    d = config_dir or default_config_dir()
    return (d / "settings.json").is_file()


def is_logged_in(config_dir: Optional[Path] = None) -> bool:
    """Best-effort: True if the sandbox appears to hold its own auth state.

    A logged-in sandbox grows a ``.credentials.json`` (file-based platforms) and
    an ``oauthAccount`` in its ``.claude.json``. We can't validate the token
    without a network call, so this only reports whether a ``/login`` has plausibly
    happened against this dir."""
    d = config_dir or default_config_dir()
    if (d / ".credentials.json").is_file():
        return True
    cj = d / ".claude.json"
    if cj.is_file():
        try:
            return "oauthAccount" in json.loads(cj.read_text())
        except (ValueError, OSError):
            return False
    return False


def build(config_dir: Optional[Path] = None, *, overwrite: bool = False) -> Path:
    """Create (or refresh) the sandbox config dir and return its path.

    Writes a minimal ``settings.json`` (empty object — no hooks, no MCP, no
    auto-memory) and nothing else. Auth is **not** seeded: the host token is not
    portable into a sandbox (see the module docstring), so the sandbox is logged
    in once, interactively, with ``CLAUDE_CONFIG_DIR=<dir> claude`` then ``/login``.
    No skills, agents, or global ``CLAUDE.md`` are ever copied — the whole point
    is that the host config does not leak in.

    ``overwrite=True`` resets the minimal ``settings.json`` but leaves any auth
    state the sandbox has already acquired in place (so a rebuild doesn't force a
    re-login)."""
    d = (config_dir or default_config_dir()).resolve()
    d.mkdir(parents=True, exist_ok=True)
    settings = d / "settings.json"
    if overwrite or not settings.exists():
        settings.write_text(json.dumps({}) + "\n")
    return d


def login_commands(config_dir: Path, *, windows: Optional[bool] = None) -> list[str]:
    """The shell command(s) to authenticate the sandbox once, per platform.

    ``windows`` forces the dialect; ``None`` auto-detects from ``os.name``. macOS
    and Linux share the POSIX form. Returns a list so callers can print each line.
    """
    win = os.name == "nt" if windows is None else windows
    d = str(config_dir)
    if win:
        # PowerShell: set the env var for the session, then run claude.
        return [f'$env:CLAUDE_CONFIG_DIR = "{d}"', "claude   # then run /login"]
    # macOS / Linux (POSIX shells): one-line env prefix.
    return [f"CLAUDE_CONFIG_DIR={d} claude   # then run /login"]


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m memeval.claudecode.sandbox`` — build/refresh the sandbox."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="memeval.claudecode.sandbox",
        description="Build a sandboxed CLAUDE_CONFIG_DIR (no host "
        "skills/agents/CLAUDE.md). Authenticate it once with a /login.",
    )
    ap.add_argument("--dir", default=None, help="sandbox path (default: eval/.claude-sandbox)")
    ap.add_argument("--overwrite", action="store_true",
                    help="reset settings.json (keeps any existing sandbox auth)")
    args = ap.parse_args(argv)

    target = Path(args.dir) if args.dir else None
    d = build(target, overwrite=args.overwrite)
    print(f"sandbox ready: {d}")
    if is_logged_in(d):
        print("  auth: already logged in to this sandbox")
        print(f"  use it:  CLAUDE_CONFIG_DIR={d} claude   (or just run memeval-bench — auto-detected)")
    else:
        print("  auth: NOT logged in — authenticate this sandbox once:")
        for line in login_commands(d):
            print(f"    {line}")
        print("  after that, memeval-bench auto-detects and uses the sandbox.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
