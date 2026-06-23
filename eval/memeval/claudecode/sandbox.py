"""A sandboxed ``CLAUDE_CONFIG_DIR`` for benchmark runs.

A normal ``claude`` invocation reads the *host* user's ``~/.claude`` — global
``CLAUDE.md``, every installed skill and agent, ``settings.json``, MCP servers.
For benchmarking the memory harness we want the opposite: a ``claude`` that sees
*only* what we explicitly hand it (the memory plugin via ``--mcp-config``) and
nothing of the host. The CLI honours ``CLAUDE_CONFIG_DIR`` — point it at a fresh
directory and the CLI keeps all its state (``.claude.json``, ``projects/``,
``sessions/``) there instead of ``~/.claude``, and discovers no host skills /
agents / global ``CLAUDE.md``.

The one thing a fresh config dir lacks is auth: it starts logged out. How that is
resolved is platform-dependent:

* **File-based-auth platforms (Linux / WSL)** — the host token is portable: it
  lives in ``~/.claude/.credentials.json`` with the matching account in
  ``~/.claude.json`` (``oauthAccount`` / ``userID``). :func:`seed_auth_from_host`
  copies both into the sandbox, so a sandboxed ``claude`` runs under the same
  subscription with no interactive step — :func:`setup_real_plugin` does this
  automatically for the ``plugin-real`` mode. Only auth crosses over; no skills,
  agents, MCP, or global ``CLAUDE.md`` are copied, so the host config still does
  not leak in. (Verified against claude-haiku: a seeded sandbox returns a real
  answer instead of "Not logged in".)
* **Keychain platforms (macOS)** — the live token is held in the OS keychain bound
  to the default config and the on-disk file is a stale leftover, so seeding is
  skipped (``seed_auth_from_host`` returns ``False`` when no on-disk credential
  exists) and the sandbox is authenticated **once, interactively**, keeping its own
  token thereafter::

    CLAUDE_CONFIG_DIR=<sandbox> claude     # then run /login

Because the sandbox dir is gitignored, neither the seeded credential nor a
freshly-minted one ever leaves the machine.

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
from typing import Callable, Optional

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


#: The Claude Code marketplace + plugin name the cookbook-memory bundle declares.
PLUGIN_MARKETPLACE = "cookbook-memory"
PLUGIN_NAME = "cookbook-memory"


def install_plugin_bundle(
    bundle_dir: str | Path, *, config_dir: Optional[Path] = None,
    claude_exe: Optional[str] = None, timeout: int = 120,
) -> None:
    """Install a built Claude Code plugin bundle into the sandbox, the native way.

    Runs the same commands a user runs — ``claude plugin marketplace add <bundle>``
    then ``claude plugin install <name> --scope user`` — with ``CLAUDE_CONFIG_DIR``
    pointed at the sandbox, so the plugin lands in the sandbox (not the host
    ``~/.claude``). Idempotent: the marketplace is removed first so a rebuilt bundle
    replaces a stale install on re-run. This consumes the plugin's own release build
    (:func:`cookbook_memory.adapters.claude_code.build.build_bundle`); it owns no
    bundling logic itself.

    Raises ``RuntimeError`` if the install fails (so a broken plugin fails the run
    loudly rather than silently benchmarking no-memory)."""
    import subprocess

    d = (config_dir or default_config_dir()).resolve()
    exe = claude_exe or _find_claude_exe()
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(d)}

    def _run(args: list[str], *, check: bool) -> subprocess.CompletedProcess:
        return subprocess.run([exe, "plugin", *args], env=env, timeout=timeout,
                              capture_output=True, text=True, check=False)

    # Best-effort clean slate (ignore failures: nothing installed yet on first run).
    _run(["uninstall", PLUGIN_NAME], check=False)
    _run(["marketplace", "remove", PLUGIN_MARKETPLACE], check=False)

    add = _run(["marketplace", "add", str(Path(bundle_dir).resolve())], check=False)
    if add.returncode != 0:
        raise RuntimeError(f"claude plugin marketplace add failed: "
                           f"{(add.stderr or add.stdout)[:400]}")
    inst = _run(["install", f"{PLUGIN_NAME}@{PLUGIN_MARKETPLACE}", "--scope", "user"],
                check=False)
    if inst.returncode != 0:
        raise RuntimeError(f"claude plugin install failed: "
                           f"{(inst.stderr or inst.stdout)[:400]}")


def _find_claude_exe() -> str:
    """Resolve the ``claude`` executable (native or WSL) via the platform detector."""
    from .platform import detect
    rt = detect()
    if rt is None:
        raise RuntimeError("claude CLI not found (native or WSL); cannot install plugin")
    return rt.exe


def _require_plugin_mcp_runtime() -> None:
    """Assert the cookbook-memory plugin can actually serve its MCP tool.

    The plugin's ``.mcp.json`` launches ``memory-cli mcp``, which needs the MCP SDK
    (the plugin's ``[mcp]`` extra). Without it the server exits on startup and the
    ``recall`` tool is silently unavailable — so we fail loudly here, the same way a
    user would discover they hadn't installed ``cookbook-memory[mcp]``."""
    import importlib.util
    import shutil

    if shutil.which("memory-cli") is None:
        raise RuntimeError(
            "memory-cli not on PATH — install the plugin's runtime "
            "(`pip install -e 'plugin[mcp]'`) so the cookbook-memory MCP server can run.")
    if importlib.util.find_spec("mcp") is None:
        raise RuntimeError(
            "the MCP SDK is not importable — install the plugin's `[mcp]` extra "
            "(`pip install -e 'plugin[mcp]'`); without it `memory-cli mcp` exits on "
            "startup and the plugin's recall tool never registers.")


def plugin_runtime_env() -> dict[str, str]:
    """The extra env a ``claude`` run needs so the installed plugin's MCP server
    resolves: ``memory-cli``'s directory prepended to ``PATH``. (``CLAUDE_PROJECT_DIR``
    is per-task and supplied by the caller.)"""
    import shutil

    cli = shutil.which("memory-cli")
    if cli is None:
        return {}
    bin_dir = str(Path(cli).resolve().parent)
    path = os.environ.get("PATH", "")
    if bin_dir in path.split(os.pathsep):
        return {}
    return {"PATH": f"{bin_dir}{os.pathsep}{path}" if path else bin_dir}


def setup_real_plugin(
    *, config_dir: Optional[Path] = None, out_dir: Optional[str | Path] = None,
    claude_exe: Optional[str] = None,
) -> dict[str, str]:
    """Build + install the real cookbook-memory plugin into the sandbox, as a user would.

    The full user-faithful setup for the ``plugin-real`` eval mode:

    1. assert the plugin's MCP runtime is present (``memory-cli`` + the MCP SDK);
    2. build the distributable bundle via the plugin's own release step
       (:func:`cookbook_memory.adapters.claude_code.build.build_bundle`);
    3. install it natively into the sandbox (:func:`install_plugin_bundle`).

    Returns the extra environment (``PATH``) a subsequent ``claude`` run needs so the
    installed plugin's ``memory-cli mcp`` server resolves. Idempotent — safe to call
    once per run before the per-task turns."""
    from cookbook_memory.adapters.claude_code.build import build_bundle

    _require_plugin_mcp_runtime()
    d = (config_dir or default_config_dir()).resolve()
    bundle = build_bundle(out_dir or (d / "_plugin-bundle"))
    install_plugin_bundle(bundle, config_dir=d, claude_exe=claude_exe)
    # The sandbox is a fresh CLAUDE_CONFIG_DIR (logged out) — without auth every
    # turn dies on "Not logged in · Please run /login". Seed the host subscription
    # so the run is non-interactive; fall back to a one-time /login on keychain
    # platforms where the on-disk token isn't portable.
    if not seed_auth_from_host(d):
        import sys
        cmds = "  " + "\n  ".join(login_commands(d))
        sys.stderr.write(
            "cookbook-memory sandbox: no portable host credential found — the "
            "sandbox is logged out and plugin-real turns will fail with "
            "'Not logged in'. Authenticate it once:\n" + cmds + "\n")
    return plugin_runtime_env()


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


def host_config_dir() -> Path:
    """The host Claude config dir (``~/.claude``) — where a non-sandboxed CLI keeps
    its auth. ``CLAUDE_CONFIG_DIR`` is intentionally ignored: we want the real host
    login, not whatever sandbox may currently be active."""
    return Path.home() / ".claude"


def _load_oauth(cred_path: Path) -> dict:
    """Return the ``claudeAiOauth`` block from ``cred_path``, or ``{}`` if missing/bad."""
    try:
        data = json.loads(cred_path.read_text())
    except (ValueError, OSError):
        return {}
    oauth = data.get("claudeAiOauth")
    return oauth if isinstance(oauth, dict) else {}


def _credential_is_fresh(cred_path: Path) -> bool:
    """True iff ``cred_path`` holds a Claude OAuth token that exists and is unexpired.

    This is the macOS guard. On macOS the live token is held in the OS Keychain and an
    on-disk ``.credentials.json`` is typically a *stale leftover*; copying it would yield
    a sandbox that looks authenticated but isn't, suppressing the ``/login`` fallback. A
    missing file, unreadable JSON, or an absent/past ``expiresAt`` (epoch ms) all read as
    not-fresh, so the caller falls back to interactive login. On Linux/WSL the on-disk
    token is the live one and is normally unexpired, so this passes."""
    import time

    expires_at = _load_oauth(cred_path).get("expiresAt")
    if not isinstance(expires_at, (int, float)):
        return False
    return expires_at > time.time() * 1000


def _credential_is_refreshable(cred_path: Path) -> bool:
    """True iff ``cred_path`` holds an OAuth block carrying a non-empty ``refreshToken``.

    An expired access token whose ``.credentials.json`` still has a ``refreshToken`` is
    recoverable: a single host-side ``claude`` invocation refreshes it in place (see
    :func:`_refresh_host_credential`). A cred with no ``refreshToken`` is genuinely dead
    and must fall back to interactive login."""
    rt = _load_oauth(cred_path).get("refreshToken")
    return isinstance(rt, str) and bool(rt.strip())


#: Env vars that, if exported, force the ``claude`` CLI into API-key mode (which fails
#: with "Invalid API key" instead of using the OAuth subscription). They MUST be unset
#: for the host token-refresh invocation. On this machine they are exported in WSL
#: ``~/.profile``, so a refresh that inherits them dies before it can refresh.
_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _refresh_host_credential(host_dir: Path, *, timeout: int = 120) -> bool:
    """Refresh the host's expired-but-refreshable OAuth token in place, once.

    Drives the real ``claude`` CLI with a trivial prompt (``claude -p "ok"``) against
    the *host* config dir (``CLAUDE_CONFIG_DIR=<host>``), with the API-key env vars
    stripped (:data:`_API_KEY_VARS`) so the CLI uses the OAuth refreshToken instead of
    falling into API-key mode. The CLI rewrites ``<host>/.credentials.json`` with a
    fresh ``accessToken``/``expiresAt`` as a side effect. Returns True iff the cred is
    fresh afterward. Best-effort: any failure (no CLI, network down, non-zero exit)
    returns False and the caller falls back to interactive login.

    The host's config dir, not the sandbox's, is refreshed deliberately: the sandbox is
    seeded from the now-fresh host token, so the sandbox never has to refresh at start of
    run — avoiding a refreshToken-rotation race against the host's stored copy."""
    import subprocess

    try:
        exe = _find_claude_exe()
    except RuntimeError:
        return False
    env = {k: v for k, v in os.environ.items() if k not in _API_KEY_VARS}
    env["CLAUDE_CONFIG_DIR"] = str(host_dir.resolve())
    try:
        subprocess.run([exe, "-p", "ok"], env=env, timeout=timeout,
                       capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return _credential_is_fresh(host_dir / ".credentials.json")


def seed_auth_from_host(
    config_dir: Optional[Path] = None, *, host_dir: Optional[Path] = None,
    refresher: "Optional[Callable[[Path], bool]]" = None,
) -> bool:
    """Copy the host's subscription auth into the sandbox so a sandboxed ``claude``
    runs under the same login — no interactive ``/login`` required.

    On file-based-auth platforms (Linux / WSL) Claude Code keeps an OAuth token in
    ``<host>/.credentials.json`` and the matching account in ``~/.claude.json``
    (``oauthAccount`` / ``userID``). Both are portable: this copies the credentials
    file into the sandbox and merges the account keys into the sandbox's
    ``.claude.json`` (creating it if absent). Only auth crosses over — no skills,
    agents, MCP, or global ``CLAUDE.md`` — so the host config still does not leak in.

    Best-effort and idempotent. Returns ``True`` only when a **live (unexpired)**
    on-disk credential was seeded; ``False`` (seeding nothing) when the host has no
    on-disk credential, or only a dead one (expired with no ``refreshToken``) — e.g. a
    macOS Keychain platform where ``.credentials.json`` is a leftover, or a host that is
    itself logged out — in which case the caller must authenticate the sandbox
    interactively (:func:`login_commands`). The freshness gate
    (:func:`_credential_is_fresh`) is what makes this safe on macOS: copying an expired
    leftover would produce a sandbox that *looks* seeded but still fails with "Not logged
    in" and no ``/login`` hint. Re-seeding on each call refreshes the token if the host
    has since rotated it.

    **Expired-but-refreshable host token (rerun robustness).** The host subscription
    token expires every ~8h. If the on-disk cred is expired but still carries a
    ``refreshToken``, it is not dead — a one-time host-side ``claude`` invocation
    refreshes it in place (``refresher``, default :func:`_refresh_host_credential`),
    after which seeding proceeds with the now-fresh token. This is preferred over
    copying the expired cred and letting the sandbox self-refresh, because a sandbox
    refresh would rotate the *shared* refreshToken mid-run and invalidate the host's
    stored copy. Refreshing the host first seeds a fresh access token, so the sandbox
    works immediately with no at-start refresh. ``refresher`` is injectable so tests
    never touch the real CLI / network.
    """
    import shutil

    d = (config_dir or default_config_dir()).resolve()
    host = (host_dir or host_config_dir()).resolve()
    host_cred = host / ".credentials.json"
    if not _credential_is_fresh(host_cred):
        # Expired but recoverable? Refresh the host token in place, then re-check.
        # No refreshToken (or refresh failed) -> genuinely dead -> caller uses /login.
        refresh = refresher or _refresh_host_credential
        if not (_credential_is_refreshable(host_cred) and refresh(host)
                and _credential_is_fresh(host_cred)):
            return False

    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(host_cred, d / ".credentials.json")

    # Merge the account identity (oauthAccount / userID) from the host's top-level
    # ~/.claude.json (it lives beside ~/.claude/, not inside it) into the sandbox's.
    host_cj = host.parent / ".claude.json"
    sb_cj = d / ".claude.json"

    def _load(p: Path) -> dict:
        try:
            return json.loads(p.read_text()) if p.is_file() else {}
        except (ValueError, OSError):
            return {}

    host_data, sb_data = _load(host_cj), _load(sb_cj)
    for key in ("oauthAccount", "userID"):
        if key in host_data:
            sb_data[key] = host_data[key]
    sb_cj.write_text(json.dumps(sb_data, indent=2) + "\n")
    return True


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
