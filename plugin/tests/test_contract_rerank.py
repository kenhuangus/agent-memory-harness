"""The reranker wiring in ``build_store`` (``$MEMORY_RERANK`` two-stage retrieve->rerank).

By default (no ``$MEMORY_RERANK``) the recall path is single-stage — ``build_store`` returns the
routed store unwrapped. When ``$MEMORY_RERANK`` is set, the routed store is wrapped in a
``RerankedStore`` that over-fetches candidates and re-scores them; the observability attrs
(``profile_name`` / ``recall_min_score``) are carried forward so the plugin still reads them off the
returned store. The offline ``mock`` reranker keeps this test network-free; ``voyage`` is the paid
captained path and is not exercised here. Stdlib + pytest only.
"""

from __future__ import annotations

import pytest

from cookbook_memory.core.contract import build_store


def _clear_env(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMEVAL_LOCAL_ANN", raising=False)
    monkeypatch.setenv("MEMORY_PROFILE", "fusion")  # force the fully-offline profile
    monkeypatch.delenv("MEMORY_RERANK", raising=False)
    monkeypatch.delenv("MEMORY_RERANK_TOP_N", raising=False)


def test_default_is_unwrapped(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    store = build_store(str(tmp_path / "s"))
    assert type(store).__name__ == "RouterStore"


def test_mock_rerank_wraps_and_preserves_attrs(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MEMORY_RERANK", "mock")
    store = build_store(str(tmp_path / "s"))
    assert type(store).__name__ == "RerankedStore"
    # observability stamps survive the wrap (the plugin reads these off the returned store)
    assert store.profile_name == "fusion"
    assert hasattr(store, "recall_min_score")


@pytest.mark.parametrize("off", ["none", "off", "", "0"])
def test_off_values_do_not_wrap(monkeypatch, tmp_path, off):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MEMORY_RERANK", off)
    store = build_store(str(tmp_path / "s"))
    assert type(store).__name__ == "RouterStore"


def test_wrapped_store_search_and_write_still_work(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("MEMORY_RERANK", "mock")
    from memeval.schema import MemoryItem

    store = build_store(str(tmp_path / "s"))
    store.write(MemoryItem(item_id="m1", content="always run ruff before committing", timestamp=1.0))
    hits = store.search("ruff lint", k=5)
    assert any(h.item_id == "m1" for h in hits)
