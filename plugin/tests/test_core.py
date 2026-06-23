"""Offline tests for the plugin core: MemoryClient recall/remember, events, fail-open.

Stdlib + pytest only; no MCP SDK, no network. A fake engine is injected via the
``build_engine`` seam so the client's behavior (events, fail-open) is verified in
isolation; a separate test exercises the real Router-backed engine over a temp store.
"""

from __future__ import annotations

import pytest

from cookbook_memory.core import Hit, MemoryClient
from cookbook_memory.core import client as client_mod
from cookbook_memory.core.config import Settings
from cookbook_memory.core.events import EventStream


class FakeEngine:
    """A minimal in-memory engine for tests (Router+stores stand-in)."""

    def __init__(self, *, boom: bool = False) -> None:
        self.items: list[tuple[str, str]] = []
        self.n = 0
        self.boom = boom

    def recall(self, query, *, k, as_of):
        if self.boom:
            raise RuntimeError("engine down")
        hits = [
            Hit(id=i, content=c, score=1.0, tokens=len(c.split()), rank=r)
            for r, (i, c) in enumerate(self.items)
            if query.lower() in c.lower()
        ]
        return hits[:k]

    def remember(self, content, *, tags, timestamp):
        if self.boom:
            raise RuntimeError("engine down")
        self.n += 1
        mem_id = f"mem-{self.n}"
        self.items.append((mem_id, content))
        return mem_id


@pytest.fixture
def inject_engine(monkeypatch):
    """Override the build_engine seam to return a chosen fake engine."""
    def _install(engine):
        monkeypatch.setattr(client_mod, "build_engine", lambda store_path: engine)
        return engine
    return _install


def _client(tmp_path, store=True) -> MemoryClient:
    return MemoryClient(store=str(tmp_path) if store else None, session_id="s1")


def test_remember_then_recall(tmp_path, inject_engine):
    inject_engine(FakeEngine())
    c = _client(tmp_path)
    assert c.remember("we chose sqlite for the store", tags=["decision"]) == "mem-1"
    hits = c.recall("sqlite")
    assert [h.id for h in hits] == ["mem-1"]
    assert hits[0].content.startswith("we chose sqlite")


def test_recall_respects_k(tmp_path, inject_engine):
    inject_engine(FakeEngine())
    c = _client(tmp_path)
    for i in range(5):
        c.remember(f"note about topic {i}")
    assert len(c.recall("topic", k=2)) == 2


def test_events_are_emitted(tmp_path, inject_engine):
    inject_engine(FakeEngine())
    c = _client(tmp_path)
    c.remember("alpha")
    c.recall("alpha")
    events = c.events.read()
    assert [e["op"] for e in events] == ["remember", "recall"]
    assert events[0]["session_id"] == "s1"
    assert events[1]["ids"] == ["mem-1"]


def test_recall_event_carries_full_hits_in_meta(tmp_path, inject_engine):
    # The recall event enriches meta.hits with content/score/rank/timestamp so a
    # reader (the eval verification step) can attribute retrieval without a second
    # store lookup. `ids` stays the top-level contract field (ADR-harness-007).
    inject_engine(FakeEngine())
    c = _client(tmp_path)
    c.remember("alpha note")
    c.recall("alpha")
    recall_ev = [e for e in c.events.read() if e["op"] == "recall"][-1]
    hits = recall_ev["meta"]["hits"]
    assert [h["id"] for h in hits] == recall_ev["ids"]
    h0 = hits[0]
    for field in ("id", "content", "score", "rank", "tokens", "timestamp"):
        assert field in h0, f"recall hit missing {field}"


def test_recall_fail_open_returns_empty_and_logs_error(tmp_path, inject_engine):
    inject_engine(FakeEngine(boom=True))
    c = _client(tmp_path)
    assert c.recall("anything") == []
    assert c.events.read()[-1]["op"] == "error"
    assert c.events.read()[-1]["meta"]["op_attempted"] == "recall"


def test_remember_fail_open_returns_empty_and_logs_error(tmp_path, inject_engine):
    inject_engine(FakeEngine(boom=True))
    c = _client(tmp_path)
    assert c.remember("anything") == ""
    assert c.events.read()[-1]["op"] == "error"
    assert c.events.read()[-1]["meta"]["op_attempted"] == "remember"


def test_client_without_store_is_fail_open(monkeypatch):
    monkeypatch.delenv("MEMORY_STORE", raising=False)
    c = MemoryClient()
    assert c.recall("x") == []
    assert c.remember("y") == ""


def test_client_with_real_engine_round_trips(tmp_path):
    # No injection: exercises the real Router-backed engine over a temp store.
    c = MemoryClient(store=str(tmp_path), session_id="s1")
    assert c.remember("we chose sqlite for the store", tags=["decision"])
    hits = c.recall("sqlite")
    assert any("sqlite" in h.content for h in hits)


def test_events_stream_none_path_is_noop():
    stream = EventStream(None)
    stream.emit("recall", query="q")  # must not raise
    assert stream.read() == []


def test_settings_from_env_resolves_events_path(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", str(tmp_path))
    s = Settings.from_env()
    assert s.store_path == tmp_path
    assert s.events_path == tmp_path / "events.jsonl"


def test_settings_from_env_strips_accidental_backticks(tmp_path):
    raw = f"`{tmp_path}/.cookbook-memory`"
    s = Settings.from_env({"MEMORY_STORE": raw})
    assert s.store_path == tmp_path / ".cookbook-memory"


def test_settings_from_env_strips_leading_backtick():
    s = Settings.from_env({"MEMORY_STORE": "`/.cookbook-memory"})
    assert s.store_path.as_posix() == "/.cookbook-memory"


def test_settings_from_env_expands_claude_project_dir(tmp_path):
    s = Settings.from_env({
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "MEMORY_STORE": "${CLAUDE_PROJECT_DIR}/.cookbook-memory",
    })
    assert s.store_path == tmp_path / ".cookbook-memory"
