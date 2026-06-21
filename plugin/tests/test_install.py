"""Offline tests for the canonical skill + the per-harness install command.

Skills are one Agent-Skills standard folder in the core (ADR-harness-009); the install
command copies (or links) them into a harness's discovery path. These tests assert the
canonical skill is well-formed and that install places it at the right path per harness.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbook_memory import cli
from cookbook_memory.core.install import HARNESS_SKILL_DIRS, canonical_skills_dir, install_skills


def test_canonical_recall_skill_is_wellformed():
    skill = canonical_skills_dir() / "recall" / "SKILL.md"
    text = skill.read_text()
    assert text.startswith("---")
    assert "name: recall" in text


def test_no_remember_skill_in_core():
    # The conscious agent is recall-only (ADR-harness-008).
    assert not (canonical_skills_dir() / "remember").exists()


@pytest.mark.parametrize("harness,subdir", sorted(HARNESS_SKILL_DIRS.items()))
def test_install_copies_skill_to_harness_path(tmp_path, monkeypatch, harness, subdir):
    monkeypatch.chdir(tmp_path)  # 'project' scope resolves to cwd
    installed = install_skills(harness, scope="project")
    dest = tmp_path / subdir / "recall" / "SKILL.md"
    assert dest.is_file()
    assert "name: recall" in dest.read_text()
    assert dest.parent in installed


def test_install_link_creates_symlink(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_skills("claude", scope="project", link=True)
    dest = tmp_path / ".claude" / "skills" / "recall"
    assert dest.is_symlink()


def test_install_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_skills("agents", scope="project")
    install_skills("agents", scope="project")  # must not raise on existing dest
    assert (tmp_path / ".agents" / "skills" / "recall" / "SKILL.md").is_file()


def test_install_unknown_harness_raises():
    with pytest.raises(ValueError):
        install_skills("emacs")


def test_cli_install_emits_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["install", "--harness", "codex", "--scope", "project"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["harness"] == "codex"
    assert any("recall" in s for s in out["skills"])
    assert (tmp_path / ".agents" / "skills" / "recall" / "SKILL.md").is_file()
