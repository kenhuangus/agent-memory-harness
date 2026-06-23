"""resolve_pipeline_version: git-tag-derived pipeline version (ADR-eval-004).

Exercises the three resolution rungs against a real throwaway git repo so the
behavior is verified end to end (no subprocess mocking): exact tag on HEAD,
nearest tag when HEAD is past it, and the MEMORY_VERSION fallback when there is
no tag (or no git). Stdlib + pytest only.
"""

from __future__ import annotations

import subprocess

import pytest

from memeval import MEMORY_VERSION
from memeval.results import normalize_version, resolve_pipeline_version


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A minimal git repo with one commit and deterministic identity."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "f.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_exact_tag_on_head(repo):
    _git(repo, "tag", "v0.3")
    info = resolve_pipeline_version(cwd=repo)
    assert info["version"] == "v0.3"
    assert info["version_exact"] is True
    assert info["untagged"] is False
    assert info["source"] == "exact-tag"
    assert info["git_sha"]  # short sha captured


def test_nearest_tag_when_head_is_past_it(repo):
    _git(repo, "tag", "v0.2")
    (repo / "f.txt").write_text("y\n", encoding="utf-8")
    _git(repo, "commit", "-aq", "-m", "second")
    info = resolve_pipeline_version(cwd=repo)
    assert info["version"] == "v0.2"
    assert info["version_exact"] is False
    assert info["untagged"] is False
    assert info["source"] == "nearest-tag"


def test_untagged_falls_back_to_memory_version(repo):
    info = resolve_pipeline_version(cwd=repo)
    assert info["version"] == normalize_version(MEMORY_VERSION)
    assert info["untagged"] is True
    assert info["source"] == "memory-version"


def test_no_git_checkout_falls_back(tmp_path):
    # A directory with no .git: resolution must degrade, never raise.
    info = resolve_pipeline_version(cwd=tmp_path)
    assert info["version"] == normalize_version(MEMORY_VERSION)
    assert info["untagged"] is True
    assert info["git_sha"] == ""
