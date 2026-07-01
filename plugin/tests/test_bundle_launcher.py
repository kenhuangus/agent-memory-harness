"""Offline tests for the bundle's runtime launcher (ADR-harness-016).

The launcher is a POSIX sh script; these tests exercise its dispatch and fail-open
behavior with stub console scripts on a controlled $PATH — no network, no real
bootstrap (bootstrap is starved by pointing $PATH at a bin dir with no uv/python3).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

LAUNCHER = (
    Path(__file__).resolve().parents[1]
    / "cookbook_memory" / "adapters" / "claude_code" / "bin" / "cookbook-memory"
)


def _write_stub(bin_dir: Path, name: str, marker: Path) -> None:
    stub = bin_dir / name
    stub.write_text(f"#!/bin/sh\necho \"$@\" > {marker}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(args, *, path: Path, home: Path, timeout: int = 30):
    env = {
        "PATH": str(path),
        "HOME": str(home),
        "COOKBOOK_MEMORY_RUNTIME": str(home / "runtime"),
    }
    return subprocess.run(
        ["/bin/sh", str(LAUNCHER), *args],
        env=env, capture_output=True, text=True, timeout=timeout,
    )


def test_launcher_is_committed_and_executable():
    assert LAUNCHER.is_file()
    assert os.access(LAUNCHER, os.X_OK)


def test_hook_mode_dispatches_to_memory_hook_on_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "marker"
    _write_stub(bin_dir, "memory-hook", marker)
    proc = _run(["hook", "Stop"], path=bin_dir, home=tmp_path / "home")
    assert proc.returncode == 0
    assert marker.read_text().strip() == "Stop"


def test_mcp_mode_dispatches_to_memory_cli_on_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "marker"
    _write_stub(bin_dir, "memory-cli", marker)
    proc = _run(["mcp"], path=bin_dir, home=tmp_path / "home")
    assert proc.returncode == 0
    assert marker.read_text().strip() == "mcp"


def test_explicit_bin_dir_override_wins_over_path(tmp_path):
    path_bin, override_bin = tmp_path / "path-bin", tmp_path / "override-bin"
    path_bin.mkdir(), override_bin.mkdir()
    path_marker, override_marker = tmp_path / "path-marker", tmp_path / "override-marker"
    _write_stub(path_bin, "memory-hook", path_marker)
    _write_stub(override_bin, "memory-hook", override_marker)
    env_home = tmp_path / "home"
    env = {
        "PATH": str(path_bin),
        "HOME": str(env_home),
        "COOKBOOK_MEMORY_RUNTIME": str(env_home / "runtime"),
        "COOKBOOK_MEMORY_BIN_DIR": str(override_bin),
    }
    proc = subprocess.run(
        ["/bin/sh", str(LAUNCHER), "hook", "SessionStart"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert override_marker.exists() and not path_marker.exists()


def test_hook_mode_is_fail_open_when_no_runtime_and_no_bootstrapper(tmp_path):
    # No memory-hook, no uv, no python3 on PATH: hook mode must still exit 0
    # immediately (a broken/absent runtime must never break the session).
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    proc = _run(["hook", "Stop"], path=empty_bin, home=tmp_path / "home")
    assert proc.returncode == 0


def test_mcp_mode_fails_loud_when_bootstrap_impossible(tmp_path):
    # MCP mode may fail (Claude Code shows the server as failed) but must say why.
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    proc = _run(["mcp"], path=empty_bin, home=tmp_path / "home")
    assert proc.returncode != 0
    assert "bootstrap" in proc.stderr


def test_unknown_mode_is_a_usage_error(tmp_path):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    proc = _run(["frobnicate"], path=empty_bin, home=tmp_path / "home")
    assert proc.returncode == 2
    assert "usage" in proc.stderr


def test_managed_venv_is_used_when_nothing_on_path(tmp_path):
    # A previously-bootstrapped managed runtime is picked up without any $PATH help.
    home = tmp_path / "home"
    venv_bin = home / "runtime" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    marker = tmp_path / "marker"
    _write_stub(venv_bin, "memory-hook", marker)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    proc = _run(["hook", "PreCompact"], path=empty_bin, home=home)
    assert proc.returncode == 0
    assert marker.read_text().strip() == "PreCompact"
