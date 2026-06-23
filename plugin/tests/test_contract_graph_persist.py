"""The graph backend persists under the store root (ADR-storage-002).

``build_store`` must give every backend a durable location under ``$MEMORY_STORE``
so memory accumulates across processes/runs. A RAM-only graph (``GraphStore()``
with no path) would evaporate each turn and silently contribute nothing to the
shared, accumulating substrate. Stdlib + pytest only; the offline (fusion) profile
needs no key and no network.
"""

from __future__ import annotations

from pathlib import Path

from cookbook_memory.core.contract import build_store
from memeval.schema import MemoryItem


def test_build_store_persists_graph_db(tmp_path, monkeypatch):
    # Force the offline profile so the test needs no embedder key.
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_PROFILE", raising=False)

    store = build_store(str(tmp_path))
    store.write(MemoryItem(item_id="m1", content="durable graph layer", timestamp=1.0))

    graph_db = tmp_path / "graph.db"
    assert graph_db.is_file(), f"graph.db missing under store root; got {sorted(p.name for p in tmp_path.iterdir())}"
    # Symmetry with the other two persistent backends.
    assert (tmp_path / "memory.db").is_file()
    assert (tmp_path / "markdown").is_dir()


def test_graph_db_survives_a_fresh_build_store(tmp_path, monkeypatch):
    # A second build_store over the same root (a new "process") reloads the graph
    # mirror rather than starting empty -- the property the shared substrate relies on.
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_PROFILE", raising=False)

    first = build_store(str(tmp_path))
    first.write(MemoryItem(item_id="g1", content="remembered across processes", timestamp=1.0))
    del first  # drop the first store's handles

    second = build_store(str(tmp_path))
    hits = second.search("remembered across processes", k=5)
    assert any(h.item_id == "g1" for h in hits), "memory did not survive a fresh build_store over the same root"
    assert (tmp_path / "graph.db").is_file()
