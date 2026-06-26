"""Isolated, authenticated ``cursor-agent`` runs (ADR-harness-014 / ADR-harness-015).

The eval harness must drive ``cursor-agent`` so that:

* it sees ONLY the MCP / config we hand it (never the developer's host ``~/.cursor``);
* it authenticates non-interactively (no keychain, no ``/login``);
* per-run / per-stage state never collides, so stages can run in parallel.

Two verified facts (see ``docs/harnesses/06-cursor-cli.md``) drive the design:

1. **`HOME` is the isolation seam — NOT `CURSOR_DATA_DIR`.** ``cursor-agent`` reads
   ``mcp.json`` / ``cli-config.json`` / auth from a hardcoded ``homedir()/.cursor/…``.
   Relocating ``HOME`` relocates all three at once; ``CURSOR_DATA_DIR`` only moves
   transcripts. So each run gets its own ``HOME`` (a fresh ``<sandbox>/.cursor``),
   plus ``CURSOR_DATA_DIR=<sandbox>/data`` so transcripts land in isolation too.
2. **`CURSOR_API_KEY` is keychain-free auth.** The binary's auth priority is
   ``auth-token → api-key → login``; the env key bypasses the keychain entirely. That
   — not a copyable sandbox — is what makes parallel per-stage runs cheap on macOS.

This module builds a sandbox ``HOME`` for a run and produces the subprocess ``env``
(``HOME`` + ``CURSOR_DATA_DIR`` + ``CURSOR_API_KEY`` + any extra).

**The memory wiring is the FAITHFUL equivalent of the Claude ``plugin-real`` flow**
(ADR-harness-013/015), not a read-only shortcut — it delivers the full
recall → answer → daydream-WRITE → accumulate loop:

* :func:`setup_real_plugin` builds the shipping Cursor plugin **bundle**
  (``cookbook_memory.adapters.cursor.build.build_bundle``) once per run; the bundle
  carries the ``recall`` MCP server at its root, loaded via ``cursor-agent
  --plugin-dir`` (Cursor's only install path). Verified: a bundled MCP server loads +
  is callable in headless ``--print``.
* :func:`write_user_hooks` writes the Daydreamer-trigger hooks into the sandbox's
  USER-level ``$HOME/.cursor/hooks.json`` (``sessionEnd`` → daydream, the ``Stop``
  analog; ``preCompact`` too). Verified: plugin-*bundled* hooks do NOT fire in
  headless ``--print``, but the SAME hooks at user level DO. So the write path fires
  exactly as Claude's ``Stop`` hook does.

The agent then drives ``cursor-agent --plugin-dir <bundle> …`` and drains the async
daydream write before the next task (mirroring ``ClaudeCodeAgent._drain_daydream``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# (no platform import needed: the bundle's --plugin-dir + --approve-mcps clear the
# MCP gate; runtime discovery lives in the agent/cli layer.)

#: The memory MCP server name as it appears in ``mcp.json`` and ``cursor-agent mcp``
#: subcommands. The model sees the tool as ``<MCP_SERVER_NAME>-recall`` (hyphen-joined,
#: per ADR-harness-015); the permission token is ``Mcp(<MCP_SERVER_NAME>:*)``.
MCP_SERVER_NAME = "cookbook-memory"
#: The recall tool's bare name on that server (``cursor-agent mcp list-tools`` form).
RECALL_TOOL = "recall"


class CursorNotAuthenticated(RuntimeError):
    """Raised when no ``CURSOR_API_KEY`` is available for a headless run."""


def api_key() -> Optional[str]:
    """The Cursor API key for headless auth, from the environment (loaded from ``.env``
    by ``memeval.dotenv_loader`` at entrypoint). ``CURSOR_API_KEY`` is preferred;
    ``CURSOR_AUTH_TOKEN`` is accepted as the higher-priority ``auth-token`` source."""
    for var in ("CURSOR_API_KEY", "CURSOR_AUTH_TOKEN"):
        v = (os.environ.get(var) or "").strip()
        if v:
            return v
    return None


def require_api_key() -> str:
    key = api_key()
    if not key:
        raise CursorNotAuthenticated(
            "No CURSOR_API_KEY (or CURSOR_AUTH_TOKEN) found. The Cursor harness "
            "authenticates headlessly with an API key (keychain-free) — generate one "
            "at https://cursor.com/dashboard and set CURSOR_API_KEY in your .env "
            "(see .env.example) or export it. See ADR-harness-014."
        )
    return key


@dataclass(slots=True)
class CursorSandbox:
    """An isolated ``HOME`` for one ``cursor-agent`` run/stage."""

    home: Path                # the sandbox HOME (its ~/.cursor lives here)
    data_dir: Path            # CURSOR_DATA_DIR (transcripts/projects)
    has_memory: bool          # whether the memory plugin is wired (bundle + hooks)
    keychain: Optional[Path] = None  # macOS: a dedicated sandbox login keychain
    plugin_dir: Optional[Path] = None  # the built --plugin-dir bundle (memory stages)

    @property
    def cursor_dir(self) -> Path:
        return self.home / ".cursor"

    @property
    def hooks_json(self) -> Path:
        return self.cursor_dir / "hooks.json"

    @property
    def transcripts_root(self) -> Path:
        """Where ``cursor-agent`` writes per-session transcripts under this sandbox."""
        return self.data_dir / "projects"


def _real_home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def build(sandbox_root: "str | Path") -> CursorSandbox:
    """Create a fresh sandbox ``HOME`` under ``sandbox_root`` (no memory wired yet).

    Refuses to use the developer's real home as a sandbox (the guard behind
    ADR-harness-014's "never mutate host config" policy). Creating the ``.cursor``
    dir makes ``cursor-agent`` resolve its config from here instead of ``~/.cursor``.

    **macOS keychain provisioning (ADR-harness-014, corrected after testing):**
    ``cursor-agent`` unconditionally probes the macOS login keychain at startup
    (``security add-generic-password`` of a ``cursor-keychain-probe`` entry) even when
    an API key is supplied. In an isolated ``HOME`` the *host* login keychain is what
    it would touch, and writing it headlessly hangs (a GUI access prompt with no TTY →
    30s timeout / "Security process exited 154"). The fix is to give the sandbox its
    OWN dedicated, unlocked, empty login keychain: ``security create-keychain`` +
    ``unlock-keychain`` into ``<HOME>/Library/Keychains/login.keychain-db``. The probe
    then writes there with no prompt, and ``--api-key`` authenticates. On non-macOS
    this is a no-op (file-based credential store)."""
    root = Path(sandbox_root).resolve()
    if root == _real_home():
        raise ValueError(
            "refusing to use the real $HOME as a cursor sandbox — pass a dedicated dir"
        )
    cursor_dir = root / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    keychain = _provision_macos_keychain(root)
    return CursorSandbox(home=root, data_dir=data_dir, has_memory=False, keychain=keychain)


def _provision_macos_keychain(home: Path) -> Optional[Path]:
    """Create + unlock a dedicated empty login keychain under the sandbox HOME (macOS
    only), so ``cursor-agent``'s startup keychain probe writes there without a GUI
    prompt. Returns the keychain path, or ``None`` on non-macOS / on any failure
    (fail-open — the run still attempts auth, just without the isolation aid)."""
    if sys.platform != "darwin":
        return None
    kc_dir = home / "Library" / "Keychains"
    kc = kc_dir / "login.keychain-db"
    try:
        kc_dir.mkdir(parents=True, exist_ok=True)
        if not kc.exists():
            subprocess.run(["/usr/bin/security", "create-keychain", "-p", "", str(kc)],
                           capture_output=True, timeout=30, check=False)
        subprocess.run(["/usr/bin/security", "unlock-keychain", "-p", "", str(kc)],
                       capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return kc if kc.exists() else None


def setup_real_plugin(sb: CursorSandbox, *, plugin_bin_dir: "str | Path | None" = None,
                      store_dir: "str | Path | None" = None) -> Path:
    """Build the shipping Cursor plugin bundle for this sandbox and wire its hooks.

    The faithful equivalent of ``claudecode.sandbox.setup_real_plugin`` (ADR-harness-013):

    1. **build the real bundle** via the plugin's own release step
       (``cookbook_memory.adapters.cursor.build.build_bundle``) — the same artifact a
       user installs, carrying the ``recall`` MCP server (root ``mcp.json``) + skills.
       The bundle's MCP command is pinned to ``plugin_bin_dir``'s interpreter (so the
       MCP server resolves the same ``cookbook_memory`` the harness uses) and, when
       given, ``store_dir`` (the per-run ``MEMORY_STORE``).
    2. **write the Daydreamer-trigger hooks at USER level** (``$HOME/.cursor/hooks.json``)
       — because plugin-bundled hooks do NOT fire headless but user-level ones do
       (verified). These route ``sessionEnd`` / ``preCompact`` to the cursor
       ``hooks_handler``, which shells out to the same ``daydream-cli`` the Claude
       ``Stop`` hook uses.

    Sets ``sb.plugin_dir`` (passed as ``--plugin-dir`` on every turn) and marks the
    sandbox memory-bearing. Returns the bundle path. Idempotent per sandbox."""
    from cookbook_memory.adapters.cursor.build import build_bundle

    bundle = build_bundle(
        sb.home / "_plugin-bundle",
        runtime_bin_dir=plugin_bin_dir,
        store_path=store_dir,
    )
    write_user_hooks(sb, plugin_python=_bundle_python(plugin_bin_dir))
    sb.plugin_dir = bundle
    sb.has_memory = True
    return bundle


def _bundle_python(plugin_bin_dir: "str | Path | None") -> str:
    """Resolve the interpreter the user-level hook command should invoke for the
    Daydreamer subprocess — the venv python that can import ``cookbook_memory`` /
    ``memeval``. Falls back to the current interpreter."""
    if plugin_bin_dir is not None:
        for name in ("python3", "python"):
            cand = Path(plugin_bin_dir) / name
            if cand.exists():
                return str(cand)
    return sys.executable or "python3"


def write_user_hooks(sb: CursorSandbox, *, plugin_python: str) -> Path:
    """Write the Daydreamer-trigger hooks into the sandbox's USER-level
    ``$HOME/.cursor/hooks.json`` (verified to fire headless, unlike bundled hooks).

    Routes ``sessionStart`` / ``sessionEnd`` / ``preCompact`` / ``postToolUse`` to the
    cursor ``hooks_handler``; only the gated events (``sessionEnd`` / ``preCompact``)
    actually fire the daydream write. Uses the plugin's own ``cursor_hooks_json`` so
    the wiring stays in one place."""
    from cookbook_memory.adapters.cursor.build import cursor_hooks_json

    sb.cursor_dir.mkdir(parents=True, exist_ok=True)
    sb.hooks_json.write_text(
        json.dumps(cursor_hooks_json(python=plugin_python), indent=2) + "\n",
        encoding="utf-8",
    )
    return sb.hooks_json


def env_for(sb: CursorSandbox, *, extra_env: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Build the subprocess environment for a ``cursor-agent`` run in this sandbox.

    Sets ``HOME`` (the isolation seam), ``CURSOR_DATA_DIR`` (transcripts in-sandbox),
    and ``CURSOR_API_KEY`` (keychain-free auth). Inherits the rest of the process env
    so ``MEMORY_STORE`` / ``OPENROUTER_API_KEY`` / ``DREAM_*`` reach the MCP server +
    daydream write path. Raises if no API key is configured."""
    key = require_api_key()
    env = dict(os.environ)
    env["HOME"] = str(sb.home)
    env["CURSOR_DATA_DIR"] = str(sb.data_dir)
    env["CURSOR_API_KEY"] = key
    if extra_env:
        env.update(extra_env)
    return env


#: The model-facing provider id for a ``--plugin-dir`` bundle is
#: ``plugin-<bundleDirName>-<server>`` (verified). The bundle dir is ``_plugin-bundle``,
#: so the recall tool the model calls is ``plugin-_plugin-bundle-cookbook-memory`` /
#: tool ``recall``. The agent matches on the structured ``toolName`` field, so this is
#: informational; ``--approve-mcps`` on the turn clears the bundle's load gate (the
#: ``mcp enable`` path is for a hand-written ``~/.cursor/mcp.json``, not bundles).
BUNDLE_PROVIDER_PREFIX = "plugin-_plugin-bundle-"


__all__ = [
    "MCP_SERVER_NAME", "RECALL_TOOL", "BUNDLE_PROVIDER_PREFIX",
    "CursorNotAuthenticated", "CursorSandbox", "api_key", "require_api_key", "build",
    "setup_real_plugin", "write_user_hooks", "env_for",
]
