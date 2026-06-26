"""Focused tests for the memory inspector — seed a temp dir, assert fan-out, classify,
anomaly flagging, probe shape, de-dup, edges, capture, and the empty/no-file-creation
and vector-dim-mismatch degradations.

Run from the repo root (needs `memeval` importable — the repo `.venv` after `make setup` —
and the repo root on PYTHONPATH so `ui` resolves; the same env `ui/run.sh`
sets up, see ui/README.md "How it runs"):
    PYTHONPATH=. python -m pytest ui/tests/ -q
Launch the inspector UI itself with `./ui/run.sh` (ui/README.md "Run").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui import fixtures
from ui.server import UIHandler, _State
from ui.substrate import open_substrate, _EmptyStore


@pytest.fixture()
def seeded(tmp_path):
    store = tmp_path / "_memory"
    manifest = fixtures.seed(str(store))
    return store, manifest


class _CaptureHandler(UIHandler):
    def __init__(self, path, substrate):
        self.path = path
        self.state = _State(substrate)   # the swappable active-substrate holder
        self.payload = None
        self.code = None

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


def test_seed_manifest(seeded):
    _, manifest = seeded
    assert manifest["total_written"] == manifest["routed_items"] + manifest["direct_writes"]
    assert any("asymmetry" in a for a in manifest["anomalies"])


def test_fanout_routed_items_land_in_all_three(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    by_id = {m["item_id"]: m for m in sub.memories()}
    # a normal routed memory fans out to all three backends (base_all)
    m = by_id["retry-max-attempts"]
    assert m["membership"] == {"markdown": True, "vectors": True, "graph": True}
    assert sorted(m["routing"]["actual_landing"]) == ["graph", "markdown", "vectors"]


def test_classify_predictions(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    by_id = {m["item_id"]: m for m in sub.memories()}
    assert by_id["retry-max-attempts"]["routing"]["classify"] == "markdown"
    assert by_id["why-rrf-fusion"]["routing"]["classify"] == "vectors"
    assert by_id["payment-depends-retry"]["routing"]["classify"] == "graph"


def test_anomaly_direct_write_is_flagged_asymmetric(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    by_id = {m["item_id"]: m for m in sub.memories()}
    a = by_id["anomaly-direct-markdown"]
    # written ONLY to markdown, bypassing the router -> landing != write_plan
    assert a["membership"] == {"markdown": True, "vectors": False, "graph": False}
    assert a["routing"]["asymmetric"] is True
    assert a["routing"]["flagged"] is True
    assert any("write_plan" in r for r in a["routing"]["flag_reasons"])
    # flagged items sort to the top of the memory list
    assert sub.memories()[0]["routing"]["flagged"] is True


def test_anomaly_intent_mismatch(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    by_id = {m["item_id"]: m for m in sub.memories()}
    a = by_id["anomaly-intent-mismatch"]
    assert a["routing"]["human_intent"] == "graph"
    assert a["routing"]["intent_mismatch"] is True
    assert a["routing"]["classify"] != "graph"


def test_ambiguous_low_margin_flagged(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    by_id = {m["item_id"]: m for m in sub.memories()}
    amb = by_id["auth-why-depends"]["routing"]
    assert amb["ambiguous"] is True
    assert amb["margin"] < sub.margin_threshold


def test_graph_edges_resolve(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    by_id = {m["item_id"]: m for m in sub.memories()}
    edges = by_id["payment-depends-retry"]["edges"]
    assert {"anchor": "depends on", "relation": "depends_on", "target": "retry-queue"} in edges


def test_dedup_unique_ids_and_markdown_canonical(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    rows = sub.memories()
    ids = [m["item_id"] for m in rows]
    assert len(ids) == len(set(ids))  # de-duped by item_id
    # markdown enriches OKF fields on the canonical (markdown-first) copy
    pay = next(m for m in rows if m["item_id"] == "payment-depends-retry")
    assert pay["okf"]["title"] is not None


def test_summary_counts_and_histogram(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    s = sub.summary()
    assert s["profile"] == "fusion"
    assert s["total_unique"] == len(sub.memories())
    assert s["misroute_count"] >= 1            # the direct-write anomaly
    assert s["ambiguous_count"] >= 2           # the two ambiguous items
    assert s["fanout_histogram"]["1"] >= 1     # the markdown-only anomaly
    assert s["fanout_histogram"]["3"] >= 10    # the routed corpus


def test_probe_shape(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    res = sub.probe("validateJwt", k=3)
    assert set(res["per_backend"].keys()) == {"markdown", "vectors", "graph"}
    assert "decision" in res and "choice" in res["decision"]
    assert "engine" in res
    assert "score_semantics" in res and set(res["score_semantics"]) == {"markdown", "vectors", "graph"}
    # a literal code-token query routes to markdown and surfaces the literal memory there
    assert res["decision"]["choice"] == "markdown"
    md_ids = [h["item_id"] for h in res["per_backend"]["markdown"]]
    assert "jwt-signature" in md_ids


def test_artifact_view_markdown_reads_real_file(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    res = sub.artifact_view("retry-max-attempts", "markdown")
    assert res["kind"] == "markdown"
    assert res["backend"] == "markdown"
    assert res["backend_present"] is True
    assert res["exists"] is True
    # md copy_path is the PER-MEMORY .md file (not a shared db), and it really exists
    assert res["copy_path"].endswith("retry-max-attempts.md")
    assert Path(res["copy_path"]).is_file()
    # the actual file is shown: parsed OKF frontmatter + raw text + body
    assert isinstance(res["frontmatter"], dict) and res["frontmatter"]
    assert "retry" in (res["text"] or "").lower()
    assert "body" in res


def test_artifact_view_vector_returns_db_path_and_embedding(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    res = sub.artifact_view("retry-max-attempts", "vectors")
    assert res["kind"] == "vector"
    # vec copy_path is the SHARED memory.db (vectors are rows, not per-memory files)
    assert res["copy_path"].endswith("memory.db")
    assert Path(res["copy_path"]).is_file()
    assert res["embedding"]["dim"]            # an int dim from the embedder
    assert res["embedding"]["index"]          # e.g. "brute-force exact"
    assert res["item"]["content"]             # the stored record


def test_artifact_view_graph_returns_db_path_node_and_edges(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    res = sub.artifact_view("payment-depends-retry", "graph")  # has an okf_links edge
    assert res["kind"] == "graph"
    assert res["copy_path"].endswith("graph.db")
    assert Path(res["copy_path"]).is_file()
    assert isinstance(res["edges"], list) and res["edges"]
    assert res["edges"][0]["target"] == "retry-queue"
    assert res["node"]["content"]


def test_artifact_view_rejects_unknown_backend_and_missing_item(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    with pytest.raises(ValueError):
        sub.artifact_view("retry-max-attempts", "nope")
    with pytest.raises(KeyError):
        sub.artifact_view("does-not-exist", "markdown")


def test_backend_artifact_endpoint(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    handler = _CaptureHandler("/api/backend-artifact?item_id=retry-max-attempts&backend=markdown", sub)
    handler.do_GET()
    assert handler.code == 200
    data = handler.payload
    assert data["item_id"] == "retry-max-attempts"
    assert data["backend"] == "markdown"
    assert data["kind"] == "markdown"
    assert data["copy_path"].endswith(".md")

    # unknown backend -> 400, missing item -> 404
    bad = _CaptureHandler("/api/backend-artifact?item_id=retry-max-attempts&backend=nope", sub)
    bad.do_GET()
    assert bad.code == 400
    missing = _CaptureHandler("/api/backend-artifact?item_id=nope&backend=markdown", sub)
    missing.do_GET()
    assert missing.code == 404


def test_reopen_swaps_active_store(seeded, tmp_path):
    storeA, _ = seeded
    storeB = tmp_path / "B" / "_memory"
    storeB.parent.mkdir(parents=True, exist_ok=True)
    fixtures.seed(str(storeB))

    h = _CaptureHandler("/api/summary", open_substrate(str(storeA), "fusion"))
    h.do_GET()
    assert h.payload["store_path"] == str(storeA)        # initially store A

    h._reopen({"store": str(storeB)})                    # live swap to store B
    assert h.code == 200
    assert h.payload["store_path"] == str(storeB)        # reopen returns B's summary

    h.path = "/api/summary"
    h.do_GET()
    assert h.payload["store_path"] == str(storeB)        # later requests see B (swap persisted)


def test_reopen_validates_input(seeded, tmp_path):
    store, _ = seeded
    h = _CaptureHandler("/api/reopen", open_substrate(str(store), "fusion"))

    h._reopen({})                                        # missing store
    assert h.code == 400
    h._reopen({"store": str(tmp_path / "does-not-exist")})  # nonexistent dir
    assert h.code == 400
    # a failed reopen leaves the original substrate active
    assert h.state.substrate.store_dir == str(store)


def test_pick_store_returns_chosen_dir(seeded, monkeypatch):
    store, _ = seeded
    import ui.server as server
    monkeypatch.setattr(server, "pick_directory", lambda initial=None: "/picked/_memory")
    h = _CaptureHandler("/api/pick-store", open_substrate(str(store), "fusion"))

    h._pick_store({"initial": str(store)})
    assert h.code == 200
    assert h.payload == {"store": "/picked/_memory"}
    # picking does NOT reopen — the client posts the path to /api/reopen itself.
    assert h.state.substrate.store_dir == str(store)


def test_pick_store_cancelled(seeded, monkeypatch):
    store, _ = seeded
    import ui.server as server
    monkeypatch.setattr(server, "pick_directory", lambda initial=None: None)
    h = _CaptureHandler("/api/pick-store", open_substrate(str(store), "fusion"))

    h._pick_store({})
    assert h.code == 200
    assert h.payload == {"cancelled": True}


def test_pick_store_unavailable(seeded, monkeypatch):
    store, _ = seeded
    import ui.server as server
    from ui.picker import PickerUnavailable

    def _boom(initial=None):
        raise PickerUnavailable("no dialog here")

    monkeypatch.setattr(server, "pick_directory", _boom)
    h = _CaptureHandler("/api/pick-store", open_substrate(str(store), "fusion"))

    h._pick_store({})
    assert h.code == 501
    assert "no dialog here" in h.payload["error"]


def test_empty_substrate_creates_no_files(tmp_path):
    empty = tmp_path / "_memory"
    empty.mkdir()
    sub = open_substrate(str(empty), "fusion")
    assert sub.memories() == []
    assert sub.summary()["total_unique"] == 0
    assert sub.probe("anything", k=3)["per_backend"]["vectors"] == []
    # the empty-substrate path must NOT create backend files
    assert not (empty / "memory.db").exists()
    assert not (empty / "graph.db").exists()
    assert not (empty / "markdown").exists()
    # all three backends fell back to the read-only empty adapter
    assert all(isinstance(sub.backends[n], _EmptyStore) for n in ("markdown", "vectors", "graph"))


def test_vector_dim_mismatch_is_caught(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")

    class _Boom:
        def search(self, *a, **k):
            raise ValueError("embedding dim mismatch: 256 != 1024")

    sub.backends["vectors"] = _Boom()
    res = sub.probe("retry", k=3)
    assert res["per_backend"]["vectors"] == []
    assert "vector probe unavailable" in res["errors"]["vectors"]
    # markdown/graph columns are unaffected
    assert "vectors" not in {k for k in res["errors"] if k != "vectors"}
    assert res["per_backend"]["markdown"]  # still returns hits


def test_capture_appends_jsonl(seeded, monkeypatch, tmp_path):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    cap = tmp_path / "captured_cases.jsonl"
    monkeypatch.setattr("ui.substrate.captured_cases_path", lambda: cap)

    r1 = sub.capture({"kind": "route", "content": "what calls foo()", "expected": {"backend": "graph"}, "note": "x"})
    assert r1["ok"] and r1["count"] == 1
    r2 = sub.capture({"kind": "retrieval", "query": "why retries", "expected": {"ids": ["a", "b"]}})
    assert r2["count"] == 2

    lines = cap.read_text().strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["kind"] == "route" and rec0["content"] == "what calls foo()"
    assert "captured_at" in rec0
    assert json.loads(lines[1])["kind"] == "retrieval"


def test_capture_rejects_bad_kind(seeded):
    store, _ = seeded
    sub = open_substrate(str(store), "fusion")
    with pytest.raises(ValueError):
        sub.capture({"kind": "nonsense", "content": "x"})


def test_resolve_store_root_descends_into_plugin_subdir(tmp_path):
    from ui.substrate import resolve_store_root
    # plugin layout: backends nested under <_memory>/.cookbook-memory/
    mem = tmp_path / "_memory"
    nested = mem / ".cookbook-memory"
    fixtures.seed(str(nested))
    assert resolve_store_root(str(mem)) == nested
    # and the inspector reads the nested corpus when pointed at the parent
    sub = open_substrate(str(mem), "fusion")
    assert sub.summary()["total_unique"] == 16
    assert any("nested plugin store dir" in w for w in sub.warnings)


def test_resolve_store_root_direct(tmp_path):
    from ui.substrate import resolve_store_root
    store = tmp_path / "_memory"
    fixtures.seed(str(store))
    assert resolve_store_root(str(store)) == store  # artifacts directly under it


def test_resolve_profile_auto_offline(monkeypatch):
    from ui.substrate import resolve_profile
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMORY_PROFILE", raising=False)
    monkeypatch.delenv("MEMEVAL_LOCAL_ANN", raising=False)
    eff, src = resolve_profile("auto")
    assert eff == "fusion" and "offline" in src
    assert resolve_profile("speed")[0] == "speed"
    with pytest.raises(ValueError):
        resolve_profile("bogus")


def test_resolve_profile_accuracy_local(monkeypatch):
    from ui.substrate import resolve_profile
    monkeypatch.delenv("MEMORY_PROFILE", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("MEMEVAL_LOCAL_ANN", raising=False)
    # explicit
    assert resolve_profile("accuracy-local") == ("accuracy-local", "explicit")
    # auto picks accuracy-local when MEMEVAL_LOCAL_ANN=1 (and no Voyage key)
    monkeypatch.setenv("MEMEVAL_LOCAL_ANN", "1")
    eff, src = resolve_profile("auto")
    assert eff == "accuracy-local" and "MEMEVAL_LOCAL_ANN" in src
    # an explicit $MEMORY_PROFILE still wins over the flag
    monkeypatch.setenv("MEMORY_PROFILE", "fusion")
    assert resolve_profile("auto")[0] == "fusion"


def test_accuracy_local_degrades_when_embedder_unavailable(seeded, monkeypatch):
    # When the MiniLM embedder can't be built (no extra / no model — the CI floor), an explicit
    # accuracy-local request must degrade to fusion with a recorded warning, never crash.
    store, _ = seeded
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    def _unavailable(warnings):
        warnings.append("accuracy-local profile unavailable (forced for test)")
        return None

    monkeypatch.setattr("ui.substrate._try_minilm", _unavailable)
    sub = open_substrate(str(store), "accuracy-local")
    assert sub.profile == "fusion"
    assert any("accuracy-local profile unavailable" in w for w in sub.warnings)
    # browse still works on the degraded substrate
    assert sub.summary()["total_unique"] > 0
