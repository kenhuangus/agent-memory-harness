"""Unit tests for the Job 5 induction (generalizer) pass — ADR-dreaming-028 §3.

The induction pass is CREATE-only: it reads post-deduction survivors, clusters
related lower-durability cards (Fix/Bug/Workaround), and synthesizes durable
Invariant/Convention cards with mandatory ``synthesized_from`` provenance. It
ships DEFAULT OFF behind ``DREAM_INDUCTION=1`` with its own call budget.

These are direct unit tests on ``_run_induction`` / ``_cluster_for_induction``
plus the env readers, so they do not need the full ``DreamingWorker.run`` pass
stack stubbed. The shared LLM-safety + redaction deps come from the package
fixtures (conftest enforces no live LLM).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("detect_secrets")

from memeval.dreaming import worker as worker_module
from memeval.dreaming.llm import Completion
from memeval.dreaming.worker import (
    InductionCard,
    InductionResult,
    _cluster_for_induction,
    _read_induction_max_calls,
    _read_use_induction,
    _run_induction,
)
from memeval.harness import InMemoryStore
from memeval.schema import MemoryItem

_NOW = 1_700_000_000.0


class _StubClient:
    """Deterministic client returning a canned completion (or a queue), or raising."""

    model = "test-model"

    def __init__(self, completion: Completion | list[Completion] | None = None,
                 *, raise_exc: Exception | None = None) -> None:
        self._next = completion
        self._raise = raise_exc
        self.calls = 0

    def complete(self, prompt: Any, *, system: Any = None, max_tokens: int = 1024) -> Completion:
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        if isinstance(self._next, list):
            return self._next.pop(0) if self._next else Completion(text="", tokens_in=0, tokens_out=0)
        return self._next or Completion(text="", tokens_in=0, tokens_out=0)


class _NoDeleteStore(InMemoryStore):
    """InMemoryStore whose ``delete`` fails the test — induction must never delete."""

    def delete(self, item_id: str) -> bool:  # pragma: no cover - asserts on misuse
        raise AssertionError("induction pass called store.delete — authority boundary violated")


def _fix(item_id: str, content: str, *, tag: str = "queryset", okf_type: str = "Fix") -> MemoryItem:
    return MemoryItem(
        item_id=item_id,
        content=content,
        timestamp=1000.0,
        tags=[tag],
        metadata={"okf_type": okf_type},
    )


def _synthesis(type_: str = "Invariant", content: str = "Always clone the queryset before mutating.",
               rationale: str = "three fixes all re-cloned") -> Completion:
    return Completion(
        text=json.dumps({"synthesis": {"type": type_, "content": content, "rationale": rationale}}),
        tokens_in=80, tokens_out=40,
    )


# --------------------------------------------------------------------------- #
# Env readers
# --------------------------------------------------------------------------- #

def test_induction_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DREAM_INDUCTION", raising=False)
    assert _read_use_induction() is False


def test_induction_enabled_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DREAM_INDUCTION", "1")
    assert _read_use_induction() is True
    monkeypatch.setenv("DREAM_INDUCTION", "true")  # only "1" enables
    assert _read_use_induction() is False


def test_induction_max_calls_default_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DREAM_INDUCTION_MAX_CALLS", raising=False)
    assert _read_induction_max_calls() == 5
    monkeypatch.setenv("DREAM_INDUCTION_MAX_CALLS", "2")
    assert _read_induction_max_calls() == 2
    monkeypatch.setenv("DREAM_INDUCTION_MAX_CALLS", "-3")
    assert _read_induction_max_calls() == 0
    monkeypatch.setenv("DREAM_INDUCTION_MAX_CALLS", "garbage")
    assert _read_induction_max_calls() == 5


# --------------------------------------------------------------------------- #
# Clustering
# --------------------------------------------------------------------------- #

def test_cluster_groups_by_type_and_tag_min_cluster() -> None:
    items = [
        _fix("a", "fix 1", tag="qs"),
        _fix("b", "fix 2", tag="qs"),
        _fix("c", "fix 3", tag="qs"),
        _fix("d", "fix 4", tag="other"),  # lone tag → no cluster
    ]
    clusters = _cluster_for_induction(items, min_cluster=3)
    assert len(clusters) == 1
    assert {it.item_id for it in clusters[0]} == {"a", "b", "c"}


def test_cluster_ignores_non_source_types() -> None:
    items = [
        _fix("a", "x", tag="qs", okf_type="Invariant"),
        _fix("b", "y", tag="qs", okf_type="Invariant"),
        _fix("c", "z", tag="qs", okf_type="Convention"),
    ]
    assert _cluster_for_induction(items, min_cluster=2) == []


# --------------------------------------------------------------------------- #
# _run_induction behaviour
# --------------------------------------------------------------------------- #

def test_run_induction_creates_card_with_provenance() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    client = _StubClient(_synthesis())

    result = _run_induction(items, store, client, max_calls=5, model="test-model",
                            session_id="s1", now=_NOW, min_cluster=3)

    assert isinstance(result, InductionResult)
    assert len(result.created) == 1
    card = result.created[0]
    assert isinstance(card, InductionCard)
    assert card.okf_type == "Invariant"
    assert card.synthesized_from == ("a", "b", "c")  # sorted source ids
    # The new card is persisted with provenance + durable type.
    stored = store.get(card.item_id)
    assert stored is not None
    assert stored.metadata["okf_type"] == "Invariant"
    assert stored.metadata["synthesized_from"] == ["a", "b", "c"]
    assert stored.source == "dream-induction"
    assert result.llm_calls == 1


def test_run_induction_is_create_only_never_deletes() -> None:
    # _NoDeleteStore raises on any delete; a happy-path synthesis must not trip it.
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    _run_induction(items, store, _StubClient(_synthesis()), max_calls=5,
                   model="test-model", session_id="s1", now=_NOW, min_cluster=3)
    # originals untouched (still present), plus exactly one new induct- card.
    ids = {it.item_id for it in store.all()}
    assert {"a", "b", "c"} <= ids
    assert sum(1 for i in ids if i.startswith("induct-")) == 1


def test_run_induction_below_min_cluster_does_nothing() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b")]
    for it in items:
        store.write(it)
    result = _run_induction(items, store, _StubClient(_synthesis()), max_calls=5,
                            model="test-model", session_id="s1", now=_NOW, min_cluster=3)
    assert result.created == []
    assert result.llm_calls == 0


def test_run_induction_budget_cap() -> None:
    store = _NoDeleteStore()
    items = (
        [_fix(f"a{i}", f"qs {i}", tag="qs") for i in range(3)]
        + [_fix(f"b{i}", f"mig {i}", tag="migration") for i in range(3)]
    )
    for it in items:
        store.write(it)
    client = _StubClient([_synthesis(), _synthesis()])
    result = _run_induction(items, store, client, max_calls=1, model="test-model",
                            session_id="s1", now=_NOW, min_cluster=3)
    assert result.llm_calls <= 1
    assert len(result.created) <= 1


def test_run_induction_fails_open_on_exception() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    result = _run_induction(items, store, _StubClient(raise_exc=RuntimeError("boom")),
                            max_calls=5, model="test-model", session_id="s1",
                            now=_NOW, min_cluster=3)
    assert result.created == []  # no raise, no card


def test_run_induction_rejects_null_synthesis() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    client = _StubClient(Completion(text=json.dumps({"synthesis": None}), tokens_in=10, tokens_out=5))
    result = _run_induction(items, store, client, max_calls=5, model="test-model",
                            session_id="s1", now=_NOW, min_cluster=3)
    assert result.created == []


def test_run_induction_drops_invalid_target_type() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    client = _StubClient(_synthesis(type_="Fix"))  # not an allowed target type
    result = _run_induction(items, store, client, max_calls=5, model="test-model",
                            session_id="s1", now=_NOW, min_cluster=3)
    assert result.created == []


def test_run_induction_idempotent_across_runs() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    r1 = _run_induction(items, store, _StubClient(_synthesis()), max_calls=5,
                        model="test-model", session_id="s1", now=_NOW, min_cluster=3)
    assert len(r1.created) == 1
    # Second run over the same cluster must not mint a duplicate card.
    r2 = _run_induction(items, store, _StubClient(_synthesis()), max_calls=5,
                        model="test-model", session_id="s1", now=_NOW, min_cluster=3)
    assert r2.created == []
    induct_cards = [i for i in store.all() if i.item_id.startswith("induct-")]
    assert len(induct_cards) == 1


def test_dreaming_worker_run_wires_induction_when_enabled(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: DREAM_INDUCTION=1 flows a synthesized card into the summary.

    All delete-authority passes are disabled (TTL/dedup/contradiction/governance)
    so the single stubbed client only services the induction pass.
    """
    from memeval.dreaming.worker import DreamingWorker

    monkeypatch.setenv("MEMORY_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "0")        # no TTL
    monkeypatch.setenv("DREAM_DEDUP_NEIGHBORHOOD", "0")
    monkeypatch.setenv("DREAM_CONTRADICTION_NEIGHBORHOOD", "0")
    monkeypatch.setenv("DREAM_CONTRADICTION_MAX_CALLS", "0")
    monkeypatch.setenv("DREAM_GOVERNANCE_MAX_CALLS", "0")
    monkeypatch.setenv("DREAM_INDUCTION", "1")
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda p: False)
    monkeypatch.setattr("memeval.dreaming._state._is_network_fs", lambda p: False)
    monkeypatch.setattr(worker_module, "_now", lambda: _NOW)
    monkeypatch.setattr(worker_module, "_make_llm_client", lambda: _StubClient(_synthesis()))

    store = InMemoryStore()
    for i in ("a", "b", "c"):
        store.write(_fix(i, f"distinct fix {i}", tag="qs"))

    result = DreamingWorker(store).run()

    assert result["counts"]["items_synthesized"] == 1
    cards = result["synthesized"]["cards"]
    assert len(cards) == 1
    assert cards[0]["okf_type"] == "Invariant"
    assert cards[0]["synthesized_from"] == ["a", "b", "c"]
    assert any(it.item_id.startswith("induct-") for it in store.all())


def test_dreaming_worker_run_induction_off_by_default(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (no DREAM_INDUCTION): zero synthesis, no induct- cards, key present."""
    from memeval.dreaming.worker import DreamingWorker

    monkeypatch.setenv("MEMORY_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("DREAM_ITEM_RETENTION_DAYS", "0")
    monkeypatch.setenv("DREAM_DEDUP_NEIGHBORHOOD", "0")
    monkeypatch.setenv("DREAM_CONTRADICTION_NEIGHBORHOOD", "0")
    monkeypatch.setenv("DREAM_CONTRADICTION_MAX_CALLS", "0")
    monkeypatch.setenv("DREAM_GOVERNANCE_MAX_CALLS", "0")
    monkeypatch.delenv("DREAM_INDUCTION", raising=False)
    monkeypatch.setattr("memeval.dreaming.worker._is_network_fs", lambda p: False)
    monkeypatch.setattr("memeval.dreaming._state._is_network_fs", lambda p: False)
    monkeypatch.setattr(worker_module, "_now", lambda: _NOW)
    monkeypatch.setattr(worker_module, "_make_llm_client", lambda: _StubClient(_synthesis()))

    store = InMemoryStore()
    for i in ("a", "b", "c"):
        store.write(_fix(i, f"distinct fix {i}", tag="qs"))

    result = DreamingWorker(store).run()
    assert result["counts"]["items_synthesized"] == 0
    assert result["synthesized"]["cards"] == []
    assert not any(it.item_id.startswith("induct-") for it in store.all())


def test_run_induction_zero_budget_noop() -> None:
    store = _NoDeleteStore()
    items = [_fix(i, f"fix {i}", tag="qs") for i in ("a", "b", "c")]
    for it in items:
        store.write(it)
    result = _run_induction(items, store, _StubClient(_synthesis()), max_calls=0,
                            model="test-model", session_id="s1", now=_NOW, min_cluster=3)
    assert result == InductionResult(created=[], llm_calls=0, tokens_in=0,
                                     tokens_out=0, cost_usd=0.0, clusters_examined=0)
