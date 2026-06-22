"""Place the canonical Agent-Skills folder into a harness's discovery path.

Skills are one open standard (https://agentskills.io): a ``SKILL.md`` folder valid in
every skills-compatible harness. The plugin keeps the canonical skill once, in this
package (``cookbook_memory/skills/``); each harness only differs in *where* it looks
for skills. This module copies (or links) the canonical skill into the target
harness's discovery path (ADR-harness-009).

Discovery paths by harness:

* ``claude``   → ``<root>/.claude/skills/`` (project) or ``~/.claude/skills/`` (user)
* ``codex``    → ``<root>/.agents/skills/`` (project) or ``~/.agents/skills/`` (user)
* ``opencode`` → ``<root>/.opencode/skills/`` (project) or ``~/.opencode/skills/``

``.agents/skills/`` is read by both Codex and OpenCode, so ``--harness agents`` targets
that shared path directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Per-harness skills directory, relative to the chosen scope root.
HARNESS_SKILL_DIRS = {
    "claude": ".claude/skills",
    "codex": ".agents/skills",
    "opencode": ".opencode/skills",
    "agents": ".agents/skills",  # the shared Codex + OpenCode path
}


def canonical_skills_dir() -> Path:
    """Return the package's canonical skills directory (the source of truth)."""
    return Path(__file__).resolve().parent.parent / "skills"


def scope_root(scope: str) -> Path:
    """Return the base directory for ``project`` (cwd) or ``user`` (home) scope."""
    if scope == "user":
        return Path.home()
    if scope == "project":
        return Path.cwd()
    raise ValueError(f"unknown scope: {scope!r} (expected 'project' or 'user')")


def install_skills(harness: str, *, scope: str = "project", link: bool = False) -> list[Path]:
    """Place every canonical skill into ``harness``'s discovery path.

    Copies each ``<canonical>/<name>/`` skill folder into the harness's skills
    directory under the chosen scope. With ``link=True`` a symlink to each canonical
    skill folder is created instead of a copy (handy for local dev; copy is the safe
    cross-platform default). Returns the list of destination skill folders.
    """
    if harness not in HARNESS_SKILL_DIRS:
        raise ValueError(
            f"unknown harness: {harness!r} (expected one of {sorted(HARNESS_SKILL_DIRS)})"
        )
    src_root = canonical_skills_dir()
    dest_root = scope_root(scope) / HARNESS_SKILL_DIRS[harness]
    dest_root.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    for skill in sorted(p for p in src_root.iterdir() if p.is_dir()):
        dest = dest_root / skill.name
        if dest.exists() or dest.is_symlink():
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            else:
                shutil.rmtree(dest)
        if link:
            dest.symlink_to(skill, target_is_directory=True)
        else:
            shutil.copytree(skill, dest)
        installed.append(dest)
    return installed


__all__ = ["install_skills", "canonical_skills_dir", "scope_root", "HARNESS_SKILL_DIRS"]
