"""Offline tests for the ``memory`` CLI — query/remember/stats/log/reset.

Drives ``cli.main`` with a temp ``$MEMORY_STORE``. Because no real Orchestrator is
wired yet, ``remember`` no-ops and ``query`` returns nothing (fail-open) — the test
asserts the CLI plumbing, JSON output, and the events stream behave, not retrieval
quality (covered against a fake Orchestrator in test_core).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbook_memory import cli


def _run(capsys, argv) -> dict:
    rc = cli.main(argv)
    assert rc == 0
    out = capsys.readouterr().out
    return json.loads(out)


def test_query_outputs_hits_json(tmp_path, capsys):
    res = _run(capsys, ["--store", str(tmp_path), "query", "anything"])
    assert res["query"] == "anything"
    assert res["hits"] == []  # fail-open: no backend wired


def test_remember_outputs_id_json(tmp_path, capsys):
    res = _run(capsys, ["--store", str(tmp_path), "remember", "a fact", "--tags", "x,y"])
    assert res["stored"] is False  # fail-open: NullOrchestrator
    assert res["id"] == ""


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
