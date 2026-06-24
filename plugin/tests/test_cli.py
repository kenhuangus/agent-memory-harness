"""Offline tests for the ``memory-cli`` — query/remember/stats/log/reset.

Drives ``cli.main`` with a temp ``$MEMORY_STORE`` backed by the real Router over the
store backends. Asserts the CLI plumbing, JSON output, the events stream, and a real
remember→query round-trip.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cookbook_memory import cli


def _run(capsys, argv) -> dict:
    rc = cli.main(argv)
    assert rc == 0
    out = capsys.readouterr().out
    return json.loads(out)


def test_remember_then_query_round_trip(tmp_path, capsys):
    res = _run(capsys, ["--store", str(tmp_path), "remember", "we chose sqlite", "--tags", "decision"])
    assert res["stored"] is True
    assert res["id"]
    res = _run(capsys, ["--store", str(tmp_path), "query", "sqlite"])
    assert res["query"] == "sqlite"
    assert any("sqlite" in h["content"] for h in res["hits"])


def test_query_empty_store_returns_no_hits(tmp_path, capsys):
    res = _run(capsys, ["--store", str(tmp_path), "query", "anything"])
    assert res["query"] == "anything"
    assert res["hits"] == []


def test_stats_counts_events(tmp_path, capsys):
    cli.main(["--store", str(tmp_path), "query", "q1"])
    cli.main(["--store", str(tmp_path), "remember", "c1"])
    capsys.readouterr()  # drain
    res = _run(capsys, ["--store", str(tmp_path), "stats"])
    assert res["total"] == 2
    assert res["by_op"]["recall"] == 1
    assert res["by_op"]["remember"] == 1


def test_log_prints_recent_events(tmp_path, capsys):
    cli.main(["--store", str(tmp_path), "query", "q1"])
    capsys.readouterr()
    cli.main(["--store", str(tmp_path), "log", "-n", "10"])
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["op"] == "recall"


def test_reset_clears_events(tmp_path, capsys):
    cli.main(["--store", str(tmp_path), "query", "q1"])
    capsys.readouterr()
    res = _run(capsys, ["--store", str(tmp_path), "reset"])
    assert res["reset"] is True
    assert not (tmp_path / "events.jsonl").exists()


def test_install_claude_plugin_builds_pinned_bundle_and_runs_claude(
    tmp_path, capsys, monkeypatch
):
    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []
    built: dict[str, str] = {}

    def fake_build_bundle(out_dir, *, runtime_bin_dir=None, **_kwargs):
        built["out_dir"] = str(out_dir)
        built["runtime_bin_dir"] = str(runtime_bin_dir)
        return Path(out_dir)

    def fake_run(cmd, *, env, text, capture_output, check):
        calls.append(cmd)
        envs.append(env)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "cookbook_memory.adapters.claude_code.build.build_bundle",
        fake_build_bundle,
    )
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/sandbox-should-not-leak")

    bundle_dir = tmp_path / "bundle"
    runtime_bin_dir = tmp_path / "venv" / "bin"
    result = _run(capsys, [
        "install-claude-plugin",
        "--bundle-dir", str(bundle_dir),
        "--runtime-bin-dir", str(runtime_bin_dir),
        "--claude", "claude-test",
    ])

    assert result["ok"] is True
    assert built == {"out_dir": str(bundle_dir), "runtime_bin_dir": str(runtime_bin_dir)}
    assert calls == [
        ["claude-test", "plugin", "uninstall", "cookbook-memory"],
        ["claude-test", "plugin", "marketplace", "remove", "cookbook-memory"],
        ["claude-test", "plugin", "marketplace", "add", str(bundle_dir)],
        ["claude-test", "plugin", "install", "cookbook-memory@cookbook-memory", "--scope", "user"],
        ["claude-test", "plugin", "details", "cookbook-memory"],
    ]
    assert all("CLAUDE_CONFIG_DIR" not in env for env in envs)


def test_default_runtime_bin_dir_resolves_memory_cli_from_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "memory-cli"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)

    monkeypatch.setattr(cli.sys, "argv", ["memory-cli"])
    monkeypatch.setenv("PATH", str(bin_dir))

    assert cli._default_runtime_bin_dir() == bin_dir.resolve()
