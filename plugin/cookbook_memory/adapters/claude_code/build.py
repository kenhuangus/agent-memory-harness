"""Build the distributable Claude Code plugin bundle.

This is the **production release step** for the Claude Code adapter: it assembles a
self-contained, installable plugin directory from the in-repo ingredients, the way
the plugin actually ships to users (ADR-harness-009, AC3 in the walking skeleton).

The repo keeps each ingredient exactly once and never duplicates content in git:

* the adapter manifests live under ``adapters/claude_code/`` (``.claude-plugin/``,
  ``.mcp.json``, ``hooks/``) — committed source;
* the ``recall`` skill lives once at ``cookbook_memory/skills/recall/`` — the
  canonical Agent-Skills folder, shared across all harnesses.

:func:`build_bundle` **materializes** these into one directory that is a valid
Claude Code plugin: manifests + MCP + hooks **plus** the skill copied into
``<out>/skills/``. That directory is what ``claude plugin install`` consumes, so a
single native install delivers skill + tools + hooks together. The materialized
output is a build artifact — generated, validatable, and git-ignored — never a
committed second copy of the skill.

Run it via ``memory-cli build-bundle --out <dir>`` or call :func:`build_bundle`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ...core.install import canonical_skills_dir

#: This adapter's committed source dir (manifests + hooks live here).
ADAPTER_DIR = Path(__file__).resolve().parent

#: The committed ingredients copied verbatim into the bundle (relative to ADAPTER_DIR).
_MANIFEST_PARTS = (".claude-plugin", ".mcp.json", "hooks")


class BundleError(RuntimeError):
    """A required ingredient is missing or the assembled bundle is invalid."""


def build_bundle(out_dir: str | Path, *, clean: bool = True) -> Path:
    """Assemble the shippable Claude Code plugin bundle at ``out_dir``; return it.

    Copies the adapter manifests (``.claude-plugin/``, ``.mcp.json``, ``hooks/``)
    and materializes every canonical skill into ``<out_dir>/skills/<name>/``. With
    ``clean`` (default) any existing ``out_dir`` is removed first for a reproducible
    build. Raises :class:`BundleError` if an ingredient is missing.
    """
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

    # Materialize the canonical skill(s) into the bundle — the one per-harness
    # difference, done here at build time rather than committed or installed by hand.
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

    validate_bundle(out)
    return out


def validate_bundle(bundle_dir: str | Path) -> None:
    """Assert a built bundle is a well-formed, installable Claude Code plugin.

    Checks the manifest, MCP config, hooks, and that at least one skill was
    materialized. Raises :class:`BundleError` on the first problem. This is the
    structural gate; ``claude plugin validate`` is the CLI-side check the release
    pipeline also runs."""
    b = Path(bundle_dir)
    checks = {
        "plugin manifest": b / ".claude-plugin" / "plugin.json",
        "MCP config": b / ".mcp.json",
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
