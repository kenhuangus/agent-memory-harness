"""Offline tests for the plugin core: recall/remember, events, fail-open.

Stdlib + pytest only; no MCP SDK, no real Orchestrator, no network. A fake
Orchestrator stands in for the storage workstream's real one so the plugin's
behavior (routing through the seam, emitting events, failing open) is verified in
isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cookbook_memory.core import Memory, build_memory
from cookbook_memory.core.config import Settings
from cookbook_memory.core.events import EventStream
from cookbook_memory.core.orchestrator import Hit, NullOrchestrator, make_orchestrator


class FakeOrchestrator:
    """A minimal in-memory Orchestrator for tests (route·rank·dedup stand-in)."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []  # (id, content)
        self.n = 0

    def recall(self, query, *, k=5, as_of=None):
        # Naive substring relevance, best-first, capped at k.
        hits = [
            Hit(id=i, content=c, score=1.0, tokens=len(c.split()), rank=r)
            for r, (i, c) in enumerate(self.items)
            if query.lower() in c.lower()
        ]
        return hits[:k]

    def remember(self, content, *, tags=None, timestamp=0.0):
        self.n += 1
        mem_id = f"mem-{self.n}"
        self.items.append((mem_id, content))
        return mem_id


class BoomOrchestrator:
    """An Orchestrator that always raises — exercises the fail-open path."""

    def recall(self, query, *, k=5, as_of=None):
        raise RuntimeError("backend down")

    def remember(self, content, *, tags=None, timestamp=0.0):
        raise RuntimeError("backend down")


def _memory(tmp_path: Path, orch) -> Memory:
    events = EventStream(tmp_path / "events.jsonl")
    return Memory(orch, events, session_id="s1", default_k=5)


def test_remember_then_recall(tmp_path):
    mem = _memory(tmp_path, FakeOrchestrator())
    mem_id = mem.remember("we chose sqlite for the store", tags=["decision"])
    assert mem_id == "mem-1"
    hits = mem.recall("sqlite")
    assert [h.id for h in hits] == ["mem-1"]
    assert hits[0].content.startswith("we chose sqlite")


def test_recall_respects_k(tmp_path):
    orch = FakeOrchestrator()
    mem = _memory(tmp_path, orch)
    for i in range(5):
        mem.remember(f"note about topic {i}")
    assert len(mem.recall("topic", k=2)) == 2


def test_events_are_emitted(tmp_path):
    mem = _memory(tmp_path, FakeOrchestrator())
    mem.remember("alpha")
    mem.recall("alpha")
    events = mem.events.read()
    ops = [e["op"] for e in events]
    assert ops == ["remember", "recall"]
    assert events[0]["session_id"] == "s1"
    assert events[1]["ids"] == ["mem-1"]


def test_recall_fail_open_returns_empty_and_logs_error(tmp_path):
    mem = _memory(tmp_path, BoomOrchestrator())
    assert mem.recall("anything") == []
    events = mem.events.read()
    assert events[-1]["op"] == "error"
    assert events[-1]["meta"]["op_attempted"] == "recall"


def test_remember_fail_open_returns_empty_and_logs_error(tmp_path):
    mem = _memory(tmp_path, BoomOrchestrator())
    assert mem.remember("anything") == ""
    events = mem.events.read()
    assert events[-1]["op"] == "error"
    assert events[-1]["meta"]["op_attempted"] == "remember"


def test_null_orchestrator_is_fail_open(tmp_path):
    mem = _memory(tmp_path, NullOrchestrator("test"))
    assert mem.recall("x") == []
    assert mem.remember("y") == ""
    # Null is not an error — it's a clean no-op; recall/remember events still logged.
    ops = [e["op"] for e in mem.events.read()]
    assert ops == ["recall", "remember"]


def test_make_orchestrator_without_store_is_null():
    orch = make_orchestrator({})
    assert isinstance(orch, NullOrchestrator)


def test_make_orchestrator_with_store_falls_back_to_null_until_backend_lands(tmp_path):
    # The real Orchestrator isn't wired yet, so even with a store it degrades.
    orch = make_orchestrator({"MEMORY_STORE": str(tmp_path)})
    assert isinstance(orch, NullOrchestrator)


def test_events_stream_none_path_is_noop():
    stream = EventStream(None)
    stream.emit("recall", query="q")  # must not raise
    assert stream.read() == []


def test_build_memory_without_store_is_usable(monkeypatch):
    monkeypatch.delenv("MEMORY_STORE", raising=False)
    mem = build_memory()
    assert mem.recall("anything") == []
    assert mem.remember("anything") == ""


def test_settings_from_env_resolves_events_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", str(tmp_path))
    s = Settings.from_env()
    assert s.store_path == tmp_path
    assert s.events_path == tmp_path / "events.jsonl"
