"""Load API keys (and other config) from the repo-root ``.env`` — one place, used by
every entrypoint that reads ``.env`` variables.

The project keeps all keys (``OPENROUTER_API_KEY`` for the daydreamer, ``MEMORY_STORE``,
``DREAM_*`` …) in a single root ``.env``. Anything that reads those at runtime calls
:func:`load_root_dotenv` first so the user never has to ``export`` them. Existing
environment variables are NEVER overridden, so an explicit ``export`` still wins.

Uses ``python-dotenv`` when installed (the ``eval[claudecode]`` extra); falls back to a
tiny stdlib parser so a missing dependency never breaks a run. Idempotent and a no-op
when no ``.env`` is found.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_LOADED: set[str] = set()  # resolved .env paths already loaded this process


def find_root_dotenv(start: "str | Path | None" = None) -> Optional[Path]:
    """Walk up from ``start`` (default cwd) to the repo root and return the ``.env`` path,
    or ``None``. Stops at the first dir holding ``.env``; otherwise stops at the repo root
    (a dir with ``.git``) so it never wanders above the project."""
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        cand = d / ".env"
        if cand.is_file():
            return cand
        if (d / ".git").exists():
            return None
    return None


def load_root_dotenv(*, start: "str | Path | None" = None, verbose: bool = False) -> Optional[Path]:
    """Load the repo-root ``.env`` (without overriding existing env vars). Returns the path
    loaded, or ``None`` when none was found. Idempotent per path within a process."""
    env_path = find_root_dotenv(start)
    if env_path is None:
        return None
    key = str(env_path)
    if key in _LOADED:
        return env_path
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    except ImportError:
        # Minimal stdlib fallback: KEY=VALUE lines; skip comments/blanks; don't override.
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            return None
    _LOADED.add(key)
    if verbose:
        print(f"loaded environment from {env_path}", flush=True)
    return env_path


__all__ = ["find_root_dotenv", "load_root_dotenv"]
