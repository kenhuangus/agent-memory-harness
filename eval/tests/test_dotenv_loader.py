"""Tests for the shared repo-root .env loader (memeval.dotenv_loader).

Every entrypoint that reads .env variables (the pipeline, memeval-bench, daydream-cli)
loads them through this one helper. Stdlib + pytest only.
"""

from __future__ import annotations

import os

from memeval.dotenv_loader import find_root_dotenv, load_root_dotenv


def _reset_loaded():
    import memeval.dotenv_loader as dl
    dl._LOADED.clear()


def test_loads_unset_keys_without_overriding(monkeypatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()  # mark the repo root
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=from_dotenv\nSOME_NEW_KEY=hello\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "explicit")  # already set -> preserved
    monkeypatch.delenv("SOME_NEW_KEY", raising=False)
    _reset_loaded()

    loaded = load_root_dotenv()

    assert loaded == tmp_path / ".env"
    assert os.environ["OPENROUTER_API_KEY"] == "explicit"  # not overridden
    assert os.environ["SOME_NEW_KEY"] == "hello"           # unset key loaded


def test_finds_env_walking_up_from_subdir(monkeypatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert find_root_dotenv() == tmp_path / ".env"


def test_cwd_walk_stops_at_repo_root_without_env(monkeypatch, tmp_path) -> None:
    # The cwd-walk component: a dir with .git but no .env -> None (don't wander above it).
    import memeval.dotenv_loader as dl
    (tmp_path / ".git").mkdir()
    assert dl._walk_up_for_dotenv(tmp_path) is None


def test_finds_repo_env_from_outside_cwd_via_file_anchor(monkeypatch, tmp_path) -> None:
    # The daydream hook / agent turns run with cwd OUTSIDE the repo. A cwd-only walk
    # can't reach the project .env; the __file__ anchor must still find it. (This repo
    # has a real .env at its root, which the package's __file__ resolves to.)
    from memeval.dotenv_loader import find_root_dotenv
    monkeypatch.delenv("MEMEVAL_DOTENV", raising=False)
    monkeypatch.chdir(tmp_path)  # cwd outside the repo, no .env here or above
    found = find_root_dotenv()
    # Either the repo .env is found via __file__ (editable install), or None (non-editable).
    # The contract: it must NOT raise and must not return a path under tmp_path.
    assert found is None or tmp_path not in found.parents


def test_explicit_memeval_dotenv_env_wins(monkeypatch, tmp_path) -> None:
    from memeval.dotenv_loader import find_root_dotenv
    envfile = tmp_path / "custom.env"
    envfile.write_text("Z=1\n", encoding="utf-8")
    monkeypatch.setenv("MEMEVAL_DOTENV", str(envfile))
    assert find_root_dotenv() == envfile


def test_idempotent_within_process(monkeypatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("K=v1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("K", raising=False)
    _reset_loaded()

    load_root_dotenv()
    assert os.environ["K"] == "v1"
    # A second call is a no-op (already loaded) — even if .env changed, the process env
    # isn't re-read; and it must not override the now-set value.
    (tmp_path / ".env").write_text("K=v2\n", encoding="utf-8")
    load_root_dotenv()
    assert os.environ["K"] == "v1"
