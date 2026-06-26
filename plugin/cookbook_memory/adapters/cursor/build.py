"""Build the distributable Cursor CLI plugin bundle.

The production release step for the Cursor adapter — the sibling of
:mod:`cookbook_memory.adapters.claude_code.build`. It assembles a self-contained
plugin directory that ``cursor-agent --plugin-dir <dir>`` loads (Cursor's only install
path; there is no ``cursor-agent plugin install`` subcommand).

Verified Cursor bundle layout (``cursor.com/docs/plugins`` + empirical
``--plugin-dir`` tests, see ``docs/harnesses/06-cursor-cli.md``):

    <bundle>/
    ├── .cursor-plugin/plugin.json      # manifest (name must match ^[a-z0-9.-]+$)
    ├── mcp.json                        # MCP servers — MUST be at the ROOT (not .cursor/)
    ├── hooks/hooks.json                # lifecycle hooks (interactive/IDE use)
    └── skills/<name>/SKILL.md          # materialized canonical skills

Important verified caveat: a ``--plugin-dir`` bundle's **MCP server loads in headless
``--print``**, but its **bundled hooks do NOT fire headless** — so for eval runs the
harness ALSO writes the daydream-trigger hooks at user level
(``$HOME/.cursor/hooks.json``). The bundle's ``hooks/`` is for the interactive IDE/CLI
where bundled hooks do fire. We ship it for completeness/faithfulness.

The repo keeps each ingredient once (no duplicated content in git): manifests + hooks
live under ``adapters/cursor/``; the ``recall`` skill lives once at
``cookbook_memory/skills/recall/``. :func:`build_bundle` materializes them into one
installable directory (a git-ignored build artifact).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...core.install import canonical_skills_dir

#: This adapter's committed source dir (manifests + hooks live here).
ADAPTER_DIR = Path(__file__).resolve().parent

#: Committed ingredients copied verbatim into the bundle (relative to ADAPTER_DIR).
#: NB: ``mcp.json`` is a ROOT file (verified requirement), ``hooks`` is a dir.
_MANIFEST_PARTS = (".cursor-plugin", "mcp.json", "hooks")


class BundleError(RuntimeError):
    """A required ingredient is missing or the assembled bundle is invalid."""


def build_bundle(
    out_dir: str | Path, *, clean: bool = True, runtime_bin_dir: str | Path | None = None,
    store_path: str | Path | None = None,
) -> Path:
    """Assemble the shippable Cursor plugin bundle at ``out_dir``; return it.

    Copies the adapter manifests (``.cursor-plugin/``, ``mcp.json``, ``hooks/``) and
    materializes every canonical skill into ``<out>/skills/<name>/``. ``clean``
    (default) removes an existing ``out_dir`` first for a reproducible build.
    ``store_path`` pins ``mcp.json``'s ``MEMORY_STORE`` to a concrete path (the eval
    harness passes the per-run substrate); left as ``${env:MEMORY_STORE}`` otherwise.
    ``runtime_bin_dir`` pins the MCP/hook commands to a specific environment's console
    scripts (local installs that shouldn't rely on the host ``python3``)."""
    out = Path(out_dir).resolve()
    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    for part in _MANIFEST_PARTS:
        src = ADAPTER_DIR / part
        if not src.exists():
            raise BundleError(f"missing adapter ingredient: {src}")
        dst = out / part
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    skills_src = canonical_skills_dir()
    if not skills_src.is_dir():
        raise BundleError(f"canonical skills dir not found: {skills_src}")
    skills_dst = out / "skills"
    skills_dst.mkdir(exist_ok=True)
    materialized = 0
    for skill in sorted(p for p in skills_src.iterdir() if p.is_dir()):
        shutil.copytree(skill, skills_dst / skill.name, dirs_exist_ok=True)
        materialized += 1
    if materialized == 0:
        raise BundleError(f"no skills found to materialize under {skills_src}")

    if store_path is not None or runtime_bin_dir is not None:
        _pin_bundle(out, store_path=store_path, runtime_bin_dir=runtime_bin_dir)

    validate_bundle(out)
    return out


def _pin_bundle(bundle_dir: Path, *, store_path: str | Path | None,
                runtime_bin_dir: str | Path | None) -> None:
    """Rewrite the bundle's ``mcp.json`` to a concrete store path and/or interpreter.

    The committed ``mcp.json`` uses ``python3 -m cookbook_memory mcp`` and
    ``${env:MEMORY_STORE}``. For a local/eval install we can pin the interpreter to a
    venv (so the MCP server resolves the same ``cookbook_memory`` the harness uses) and
    the store to a concrete path."""
    mcp_path = bundle_dir / "mcp.json"
    mcp = json.loads(mcp_path.read_text())
    server = mcp["mcpServers"]["cookbook-memory"]
    if runtime_bin_dir is not None:
        bin_dir = Path(runtime_bin_dir).resolve()
        py = bin_dir / "python3"
        if not py.exists():
            py = bin_dir / "python"
        if not py.exists():
            raise BundleError(f"no python in runtime bin dir: {bin_dir}")
        server["command"] = str(py)
        server["args"] = ["-m", "cookbook_memory", "mcp"]
        server.setdefault("env", {})["PATH"] = f"{bin_dir}:$PATH"
    if store_path is not None:
        server.setdefault("env", {})["MEMORY_STORE"] = str(Path(store_path).resolve())
    mcp_path.write_text(json.dumps(mcp, indent=2) + "\n")


def validate_bundle(bundle_dir: str | Path) -> None:
    """Assert a built bundle is a well-formed, installable Cursor plugin.

    Checks the manifest (root-level ``mcp.json``, ``.cursor-plugin/plugin.json``,
    ``hooks/hooks.json``) and that at least one skill was materialized. Raises
    :class:`BundleError` on the first problem."""
    b = Path(bundle_dir)
    checks = {
        "plugin manifest": b / ".cursor-plugin" / "plugin.json",
        "MCP config (root)": b / "mcp.json",
        "hooks": b / "hooks" / "hooks.json",
    }
    for label, path in checks.items():
        if not path.is_file():
            raise BundleError(f"bundle missing {label}: {path}")
    skills = b / "skills"
    if not skills.is_dir() or not any(
        (d / "SKILL.md").is_file() for d in skills.iterdir() if d.is_dir()
    ):
        raise BundleError(f"bundle has no materialized skill under {skills}")


def cursor_hooks_json(*, python: str = "python3") -> dict:
    """Return the hooks.json document the harness writes at USER level
    (``$HOME/.cursor/hooks.json``) for headless eval runs.

    Verified: plugin-bundled hooks do NOT fire in headless ``cursor-agent --print``,
    but the SAME hooks at user level DO. So the eval sandbox installs these (routing
    every gated event to this adapter's ``hooks_handler``), while the bundle's
    ``hooks/hooks.json`` covers the interactive case. ``sessionEnd`` is the Daydream
    write trigger (Cursor's ``Stop`` analog); ``preCompact`` the pre-compaction one."""
    def _cmd(event: str) -> str:
        return f"{python} -m cookbook_memory.adapters.cursor.hooks_handler {event}"
    return {
        "version": 1,
        "hooks": {
            "sessionStart": [{"command": _cmd("sessionStart")}],
            "sessionEnd": [{"command": _cmd("sessionEnd")}],
            "preCompact": [{"command": _cmd("preCompact")}],
            "postToolUse": [{"command": _cmd("postToolUse")}],
        },
    }
