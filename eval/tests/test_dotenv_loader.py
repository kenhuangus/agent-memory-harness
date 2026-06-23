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


def test_noop_when_absent(monkeypatch, tmp_path) -> None:
    # A dir with .git but no .env -> stop at the root, load nothing, never raise.
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    _reset_loaded()
    assert load_root_dotenv() is None


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
