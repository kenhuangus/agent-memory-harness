"""A sandboxed ``CLAUDE_CONFIG_DIR`` for benchmark runs.

A normal ``claude`` invocation reads the *host* user's ``~/.claude`` — global
``CLAUDE.md``, every installed skill and agent, ``settings.json``, MCP servers.
For benchmarking the memory harness we want the opposite: a ``claude`` that sees
*only* what we explicitly hand it (the memory plugin via ``--mcp-config``) and
nothing of the host. The CLI honours ``CLAUDE_CONFIG_DIR`` — point it at a fresh
directory and the CLI keeps all its state (``.claude.json``, ``projects/``,
``sessions/``) there instead of ``~/.claude``, and discovers no host skills /
agents / global ``CLAUDE.md``.

The one thing a fresh config dir lacks is auth: it is logged out. Subscription
auth is a single file — ``~/.claude/.credentials.json`` (an OAuth token) — so we
*seed only that file* into the sandbox. Nothing else from the host crosses the
boundary. (If the host stores creds in the OS keychain rather than a file there
is nothing to copy; the sandbox is then logged-out and a one-time ``claude``
``/login`` against ``CLAUDE_CONFIG_DIR=<sandbox>`` is required.)

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
import shutil
from pathlib import Path
from typing import Optional

#: Env var: explicit path to a config dir to sandbox into (highest precedence).
ENV_CONFIG_DIR = "MEMEVAL_SANDBOX_CONFIG_DIR"
#: Env var: set to a falsey value to force-disable sandboxing.
ENV_TOGGLE = "MEMEVAL_SANDBOX"

_FALSEY = {"0", "false", "no", "off", ""}

#: The host credential file that carries subscription (OAuth) auth.
_HOST_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"


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
    return str(default) if default.is_dir() else None


def is_built(config_dir: Optional[Path] = None) -> bool:
    """True if the sandbox dir exists and carries a seeded credential."""
    d = config_dir or default_config_dir()
    return (d / ".credentials.json").is_file()


def build(
    config_dir: Optional[Path] = None,
    *,
    seed_credentials: bool = True,
    overwrite: bool = False,
) -> Path:
    """Create (or refresh) the sandbox config dir and return its path.

    Writes a minimal ``settings.json`` (empty object — no hooks, no MCP, no
    auto-memory) and, when ``seed_credentials`` is set and the host has a
    file-based credential, copies *only* ``~/.claude/.credentials.json`` in so
    subscription auth works. No skills, agents, or global ``CLAUDE.md`` are ever
    copied — the whole point is that the host config does not leak in.

    ``overwrite=True`` removes any existing sandbox first (a clean rebuild).
    Raises ``FileNotFoundError`` if ``seed_credentials`` is set but the host has
    no file-based credential to copy (keychain-only auth) — the caller can retry
    with ``seed_credentials=False`` and ``/login`` into the sandbox by hand.
    """
    d = (config_dir or default_config_dir()).resolve()
    if overwrite and d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)

    settings = d / "settings.json"
    if overwrite or not settings.exists():
        settings.write_text(json.dumps({}) + "\n")

    if seed_credentials:
        if not _HOST_CREDENTIALS.is_file():
            raise FileNotFoundError(
                f"No file-based host credential at {_HOST_CREDENTIALS} to seed "
                "(keychain-only auth?). Re-run with seed_credentials=False and "
                f"run `CLAUDE_CONFIG_DIR={d} claude` then /login once."
            )
        dst = d / ".credentials.json"
        shutil.copy2(_HOST_CREDENTIALS, dst)
        os.chmod(dst, 0o600)
    return d


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m memeval.claudecode.sandbox`` — build/refresh the sandbox."""
    import argparse

    ap = argparse.ArgumentParser(
        prog="memeval.claudecode.sandbox",
        description="Build a sandboxed CLAUDE_CONFIG_DIR seeded with only the "
        "host subscription credential (no host skills/agents/CLAUDE.md).",
    )
    ap.add_argument("--dir", default=None, help="sandbox path (default: eval/.claude-sandbox)")
    ap.add_argument("--no-creds", action="store_true",
                    help="do not seed the host credential (log in by hand later)")
    ap.add_argument("--overwrite", action="store_true", help="clean rebuild")
    args = ap.parse_args(argv)

    target = Path(args.dir) if args.dir else None
    try:
        d = build(target, seed_credentials=not args.no_creds, overwrite=args.overwrite)
    except FileNotFoundError as e:
        print(f"error: {e}")
        return 1
    seeded = is_built(d)
    print(f"sandbox ready: {d}")
    print(f"  credential seeded: {'yes' if seeded else 'no (run /login against this dir)'}")
    print(f"  use it:  CLAUDE_CONFIG_DIR={d} claude   (or just run memeval-bench — auto-detected)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
