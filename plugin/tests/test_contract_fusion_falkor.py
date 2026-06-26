"""The opt-in ``fusion-falkor`` profile in ``build_store`` (offline — no live FalkorDB).

``fusion-falkor`` is the ``fusion`` routing config with ONLY its graph backend swapped for an
EXTERNAL FalkorDB the plugin CONNECTS to (``$FALKORDB_URL``, ``native=True``). These tests pin the
wiring without a real server by patching ``FalkorGraphStore`` at its module seam. They assert:

* every NON-falkor profile is byte-identical — graph stays the in-memory ``GraphStore`` and falkordb
  is never imported/connected;
* ``fusion-falkor`` constructs ``FalkorGraphStore(url=$FALKORDB_URL, native=True)`` (default url
  ``redis://localhost:6379``) and keeps every other backend on the fusion path;
* a failed connection probe fails **loud** (a clear ``$FALKORDB_URL`` error) and does NOT fall back
  to the in-memory ``GraphStore`` — a silent fallback would mislabel the graph backend.

Stdlib + pytest only; the offline (fusion) profile needs no key and no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cookbook_memory.core.contract import build_store
from memeval.stores import Fts5Store, GraphStore, MarkdownStore, SqliteVectorStore
from memeval.stores.graph_store import _MAX_DEPTH

# The dependency seam: build_store lazy-imports FalkorGraphStore from this module only under
# fusion-falkor, so patching it here both injects a fake and proves whether it was touched at all.
FALKOR_CLASS = "memeval.stores.falkor_store.FalkorGraphStore"


def _clear_selection_env(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMEVAL_LOCAL_ANN", raising=False)
    monkeypatch.delenv("FALKORDB_URL", raising=False)


@pytest.mark.parametrize(
    "profile", [None, "fusion", "speed", "fusion-local", "accuracy-local"])
def test_non_falkor_profiles_use_in_memory_graphstore(tmp_path, monkeypatch, profile):
    # Every NON-falkor profile (incl. the local profiles, which degrade to fusion offline): the graph
    # backend is the in-memory GraphStore and FalkorDB is never imported or connected. A
    # must-not-be-called FalkorGraphStore proves the path is untouched.
    _clear_selection_env(monkeypatch)
    if profile is None:
        monkeypatch.delenv("MEMORY_PROFILE", raising=False)
    else:
        monkeypatch.setenv("MEMORY_PROFILE", profile)

    with patch(FALKOR_CLASS) as falkor:
        store = build_store(str(tmp_path))

    falkor.assert_not_called()
    assert isinstance(store._router.backends["graph"], GraphStore)
    assert (tmp_path / "graph.db").is_file()


def test_default_build_never_imports_the_falkordb_client(tmp_path, monkeypatch):
    # Ground-truth lazy-import proof: a default (fusion) build_store must not pull the `falkordb`
    # CLIENT library into the process at all (it is imported only inside FalkorGraphStore.connect(),
    # reached only under fusion-falkor). Asserted on sys.modules WITHOUT patching the seam.
    import sys

    _clear_selection_env(monkeypatch)
    monkeypatch.delenv("MEMORY_PROFILE", raising=False)
    sys.modules.pop("falkordb", None)
    build_store(str(tmp_path))
    assert "falkordb" not in sys.modules


def test_fusion_falkor_constructs_falkor_graph_and_keeps_fusion_backends(tmp_path, monkeypatch):
    _clear_selection_env(monkeypatch)
    monkeypatch.setenv("MEMORY_PROFILE", "fusion-falkor")
    monkeypatch.setenv("FALKORDB_URL", "redis://falkor.example:6390")

    fake_graph = MagicMock(name="FalkorGraphStore_instance")
    with patch(FALKOR_CLASS, return_value=fake_graph) as falkor:
        store = build_store(str(tmp_path))

    # The graph backend is the FalkorGraphStore built from $FALKORDB_URL with native=True and the same
    # default traversal depth the in-memory GraphStore uses.
    falkor.assert_called_once()
    _, kwargs = falkor.call_args
    assert kwargs["url"] == "redis://falkor.example:6390"
    assert kwargs["native"] is True
    assert kwargs["max_depth"] == _MAX_DEPTH

    router = store._router
    assert router.backends["graph"] is fake_graph
    # Routing config IS the fusion profile, relabelled fusion-falkor (only the backend differs).
    assert router._config.profile_name == "fusion-falkor"
    assert router._config.consult2.enabled
    assert router._config.classifier is None
    # Every OTHER backend is identical to the fusion path.
    assert isinstance(router.backends["vectors"], SqliteVectorStore)
    assert isinstance(router.backends["markdown"], MarkdownStore)
    assert isinstance(router.backends["fts5"], Fts5Store)
    # The external server holds the graph — no in-memory graph.db mirror is created.
    assert not (tmp_path / "graph.db").exists()


def test_fusion_falkor_defaults_url_to_localhost(tmp_path, monkeypatch):
    # $FALKORDB_URL unset -> the documented redis://localhost:6379 default.
    _clear_selection_env(monkeypatch)
    monkeypatch.setenv("MEMORY_PROFILE", "fusion-falkor")
    monkeypatch.delenv("FALKORDB_URL", raising=False)

    with patch(FALKOR_CLASS, return_value=MagicMock()) as falkor:
        build_store(str(tmp_path))

    _, kwargs = falkor.call_args
    assert kwargs["url"] == "redis://localhost:6379"


def test_fusion_falkor_fails_loud_when_unreachable(tmp_path, monkeypatch):
    # The constructor round-trip is the reachability probe; when it fails, build_store must raise a
    # clear $FALKORDB_URL error and must NOT silently fall back to the in-memory GraphStore.
    _clear_selection_env(monkeypatch)
    monkeypatch.setenv("MEMORY_PROFILE", "fusion-falkor")
    monkeypatch.setenv("FALKORDB_URL", "redis://unreachable.example:6379")

    with patch(FALKOR_CLASS, side_effect=ConnectionError("connection refused")):
        with pytest.raises(RuntimeError) as excinfo:
            build_store(str(tmp_path))

    msg = str(excinfo.value)
    assert "FALKORDB_URL" in msg
    assert "redis://unreachable.example:6379" in msg
    # No silent fallback: the in-memory GraphStore mirror was never created.
    assert not (tmp_path / "graph.db").exists()
