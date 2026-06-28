"""ExpandedQueryStore + offline expanders (query-expansion retrieval, suggestion1.md idea 2)."""

from __future__ import annotations

from memeval.schema import MemoryItem
from memeval.stores.query_expand import (
    ExpandedQueryStore,
    LLMQueryExpander,
    MockQueryExpander,
)
from memeval.stores.sqlite_store import SqliteVectorStore


def test_mock_expander_produces_form_variants():
    exp = MockQueryExpander(max_variants=4)
    out = exp("run tests")
    assert "run test" in out          # plural toggle on last token
    assert "run tests" not in out     # original excluded


def test_llm_expander_degrades_on_failure():
    class Boom:
        def complete(self, *a, **k):
            raise RuntimeError("no key")
    assert LLMQueryExpander(Boom())("anything") == []


def test_expanded_store_merges_and_delegates(tmp_path):
    inner = SqliteVectorStore(str(tmp_path / "m.db"))
    inner.write(MemoryItem(item_id="a", content="always run the tests before pushing", timestamp=1.0))
    inner.write(MemoryItem(item_id="b", content="configure ruff for linting", timestamp=1.0))
    store = ExpandedQueryStore(inner, MockQueryExpander())
    hits = store.search("run the test", k=5)   # singular; expansion adds 'tests'
    ids = [h.item_id for h in hits]
    assert "a" in ids
    # delegation: write/get/all still work through the facade
    store.write(MemoryItem(item_id="c", content="pin dependency versions", timestamp=1.0))
    assert store.get("c") is not None
    assert len(store.all()) == 3


def test_non_positive_k_short_circuits(tmp_path):
    inner = SqliteVectorStore(str(tmp_path / "m.db"))
    calls = {"n": 0}
    def boom(_q):
        calls["n"] += 1
        return []
    store = ExpandedQueryStore(inner, boom)
    assert store.search("x", k=0) == []
    assert calls["n"] == 0  # expander never called for k<=0
