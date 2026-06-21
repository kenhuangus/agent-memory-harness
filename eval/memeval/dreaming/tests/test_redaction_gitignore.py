"""Rubric §S (gitignore) — ADR-011 §Consequences "Policy — gitignore"
requires the audit file pattern to actually be ignored by git, not just
listed as text.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

def _find_repo_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("could not find repo root (no .git directory)")


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
GITIGNORE = REPO_ROOT / ".gitignore"

pytestmark = pytest.mark.skipif(
    not shutil.which("git"),
    reason="git not on PATH — cannot run check-ignore tests",
)


def test_repo_gitignore_exists() -> None:
    """§110: the repo root .gitignore exists."""
    assert GITIGNORE.exists(), f"{GITIGNORE} is missing"


def test_gitignore_contains_redact_audit_pattern() -> None:
    """§111: the .gitignore contains the literal `*.redact-audit.jsonl` line.

    Matches as a standalone line (not as a substring of a comment), per the
    rubric's explicit framing.
    """
    lines = [
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "*.redact-audit.jsonl" in lines, (
        f"expected `*.redact-audit.jsonl` as a non-comment line in .gitignore; "
        f"got: {lines!r}"
    )


def test_gitignore_contains_daydream_events_pattern() -> None:
    """ADR-009 §Consequences "Policy — gitignore" mirror."""
    lines = [
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "*.daydream-events.jsonl" in lines


def _check_ignore(relpath: str) -> bool:
    """Return True if `git check-ignore` reports the path as ignored."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", relpath],
        capture_output=True,
        text=True,
    )
    # Exit code 0 = matched (ignored). Exit code 1 = not matched.
    return result.returncode == 0


def test_gitignore_pattern_actually_ignores_audit_at_root() -> None:
    """§112: the pattern actually causes git to ignore a synthetic file
    at the repo root."""
    assert _check_ignore("synthetic.redact-audit.jsonl"), (
        "git check-ignore says *.redact-audit.jsonl pattern is NOT active "
        "at the repo root — the .gitignore line is text-only, not effective"
    )


def test_gitignore_pattern_actually_ignores_audit_at_nested_path() -> None:
    """§112: the pattern works at a nested path too."""
    assert _check_ignore("eval/memeval/dreaming/sessions/abc.redact-audit.jsonl"), (
        "git check-ignore says *.redact-audit.jsonl pattern is NOT active "
        "at nested paths"
    )


def test_gitignore_pattern_actually_ignores_daydream_events() -> None:
    """ADR-009 mirror: events diary pattern is also effective."""
    assert _check_ignore("synthetic.daydream-events.jsonl")
