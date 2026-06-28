"""CoverageRerankStore — deterministic key-coverage re-rank (suggestion1.md idea 3)."""

from __future__ import annotations

from memeval.schema import MemoryItem
from memeval.stores.coverage_rank import CoverageRerankStore, key_tokens
from memeval.stores.sqlite_store import SqliteVectorStore


def test_key_tokens_drops_stopwords_and_short():
    assert key_tokens("How to use the parser") == {"parser"}  # how/to/use/the dropped, len<3 gone


def test_coverage_promotes_higher_key_overlap(tmp_path):
    inner = SqliteVectorStore(str(tmp_path / "m.db"))
    # both written; query keys = {parser, unicode}. item 'good' contains both; 'weak' neither.
    inner.write(MemoryItem(item_id="good", content="the parser must handle unicode normalization", timestamp=1.0))
    inner.write(MemoryItem(item_id="weak", content="run the linter before committing changes", timestamp=1.0))
    store = CoverageRerankStore(inner, alpha=0.0)  # pure coverage to isolate the signal
    hits = store.search("parser unicode", k=2)
    assert hits[0].item_id == "good"


def test_delegation_and_empty_query(tmp_path):
    inner = SqliteVectorStore(str(tmp_path / "m.db"))
    inner.write(MemoryItem(item_id="a", content="pin dependency versions", timestamp=1.0))
    store = CoverageRerankStore(inner)
    # write/get/all/delete delegate
    store.write(MemoryItem(item_id="b", content="enable ruff", timestamp=1.0))
    assert store.get("b") is not None
    assert len(store.all()) == 2
    assert store.delete("b") is True
    # k<=0 short-circuits
    assert store.search("anything", k=0) == []
