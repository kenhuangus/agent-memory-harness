"""Graph durability eval — the `path=` seam persists the graph so it survives a process restart.

Owner: Brent. Eval-first: written before the seam it gates.

The graph is the ONLY backend that evaporates on process exit (vectors → `memory.db`, markdown →
`markdown/`, graph → RAM). For an end-to-end test across all three *durable* backends the graph must
persist too. This adds a stdlib `path=` seam: `GraphStore(path=".../graph.db")` mirrors its nodes to a
SQLite file (WAL, mirroring `SqliteVectorStore`) and **rebuilds the typed edge indexes on load from each
node's `okf_links`** (nodes are the single source of truth — edges are derived, never separately stored).
`path=None` (the default) stays **pure in-memory / byte-equivalent** (offline zero-dependency).

**ANTI-THEATER:** durability is proved by reconstructing from disk in a SEPARATE `GraphStore` instance
(not the writer), and a fresh store at a *different empty path* sees nothing — so a pass means the data
came from the FILE, not residual RAM or a process global. Typed/directional traversal + `as_of` + the
latest-version-wins update must all survive the round-trip.

Run from `eval/`:  python3 -m unittest memeval.stores.tests.test_graph_durability
"""

from __future__ import annotations

import os
import tempfile
import unittest

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore


def _item(iid: str, content: str, links=None, ts: float = 0.0) -> MemoryItem:
    md = {"okf_title": iid}
    if links is not None:
        md["okf_links"] = links
    return MemoryItem(item_id=iid, content=content, timestamp=ts, metadata=md)


def _ids(hits) -> list:
    return [h.item_id for h in hits]


class GraphDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "graph.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, store: GraphStore) -> None:
        # coined single-token contents so seeding is unambiguous; a typed depends_on edge + a generic
        # relates_to edge, both into beta, at distinct timestamps (for the as_of check).
        store.write(_item("alpha", "alpha", [["depends on", "beta"]], ts=1.0))
        store.write(_item("beta", "beta", [], ts=2.0))
        store.write(_item("charlie", "charlie", [["relates to", "beta"]], ts=3.0))

    def test_path_none_stays_in_memory(self) -> None:
        # No path -> nothing persisted; the offline path is unaffected (byte-equivalence of that path is
        # also covered by the existing graph suites).
        g = GraphStore()  # path=None default
        self._seed(g)
        self.assertEqual(len(g.all()), 3)
        self.assertIsNone(getattr(g, "path", None), "default GraphStore carries no durable path")

    def test_nodes_and_typed_edges_survive_reload(self) -> None:
        g1 = GraphStore(path=self.path)
        self._seed(g1)
        g2 = GraphStore(path=self.path)  # SEPARATE instance, same file
        self.assertEqual({it.item_id for it in g2.all()}, {"alpha", "beta", "charlie"},
                         "all 3 nodes persisted + reloaded from disk")
        # the depends_on edge was rebuilt from the persisted okf_links: alpha -> beta is reachable.
        self.assertIn("beta", _ids(g2.search("alpha", k=5)),
                      "typed edge rebuilt from disk -> beta reachable from alpha")

    def test_reload_reads_the_file_not_residual_state(self) -> None:
        # ANTI-THEATER: the data must come from the FILE, not the writer's RAM or a global.
        g1 = GraphStore(path=self.path)
        self._seed(g1)
        other = GraphStore(path=os.path.join(self._tmp.name, "other.db"))
        self.assertEqual(other.all(), [], "a fresh store at a DIFFERENT empty path has no data")
        g2 = GraphStore(path=self.path)
        self.assertEqual(len(g2.all()), 3, "a store at the WRITTEN path loads the persisted nodes")

    def test_directional_traversal_survives_reload(self) -> None:
        g1 = GraphStore(path=self.path)
        self._seed(g1)
        g2 = GraphStore(path=self.path)
        # 'what depends on beta' is an IN query on depends_on -> alpha (reached via the reverse index
        # rebuilt from disk).
        self.assertIn("alpha", _ids(g2.search("what depends on beta", k=5)),
                      "reverse (_in) index rebuilt from disk -> alpha reachable as a depends_on in-edge")

    def test_as_of_survives_reload(self) -> None:
        g1 = GraphStore(path=self.path)
        self._seed(g1)
        g2 = GraphStore(path=self.path)
        # charlie.ts=3.0; an as_of=2.5 must still exclude it after reload (timestamp persisted).
        self.assertNotIn("charlie", _ids(g2.search("charlie", k=5, as_of=2.5)),
                         "persisted timestamp honored -> future node filtered by as_of after reload")

    def test_update_persists_latest_version(self) -> None:
        g1 = GraphStore(path=self.path)
        g1.write(_item("alpha", "first", [], ts=1.0))
        g1.write(_item("alpha", "second", [], ts=5.0))
        g2 = GraphStore(path=self.path)
        self.assertEqual(g2.get("alpha").content, "second", "latest write persists (INSERT OR REPLACE)")
        self.assertEqual(len([i for i in g2.all() if i.item_id == "alpha"]), 1,
                         "one row per item_id after an in-place update")

    def test_write_after_close_fails_loud(self) -> None:
        # A closed file-backed store must NOT silently accept writes into RAM that never persist.
        g = GraphStore(path=self.path)
        g.write(_item("alpha", "alpha", [], ts=1.0))
        g.close()
        with self.assertRaises(RuntimeError):
            g.write(_item("beta", "beta", [], ts=2.0))
        self.assertIsNone(g.get("beta"), "the rejected post-close write left no trace in RAM either")
        g2 = GraphStore(path=self.path)
        self.assertEqual({it.item_id for it in g2.all()}, {"alpha"},
                         "the post-close write must not have persisted (fail-loud, not RAM-only)")

    def test_failed_durable_write_is_atomic(self) -> None:
        # ATOMICITY: a durable write that fails to persist (here: non-JSON-serializable metadata) must
        # leave BOTH the in-memory graph and disk untouched — persist runs before the RAM mutation.
        g = GraphStore(path=self.path)
        g.write(_item("ok", "ok", [], ts=1.0))
        with self.assertRaises(TypeError):
            g.write(MemoryItem(item_id="bad", content="bad", metadata={"x": {1, 2}}))  # a set -> not JSON
        self.assertIsNone(g.get("bad"), "a failed durable write must not appear in the live RAM graph")
        self.assertEqual({it.item_id for it in g.all()}, {"ok"}, "only the good node is present in RAM")
        g2 = GraphStore(path=self.path)
        self.assertEqual({it.item_id for it in g2.all()}, {"ok"}, "and nothing bad persisted to disk")

    def test_all_fields_survive_reload(self) -> None:
        # Full-field fidelity: every persisted MemoryItem field round-trips through reload.
        g1 = GraphStore(path=self.path)
        g1.write(MemoryItem(item_id="x1", content="payload", timestamp=7.5, relevancy=0.3,
                            session_id="sess-9", source="unit", tags=["a", "b"], tokens=42,
                            version=4, metadata={"okf_title": "x1", "k": "v"}))
        got = GraphStore(path=self.path).get("x1")
        self.assertIsNotNone(got)
        self.assertEqual(
            (got.content, got.timestamp, got.relevancy, got.session_id, got.source,
             got.tags, got.tokens, got.version, got.metadata.get("k")),
            ("payload", 7.5, 0.3, "sess-9", "unit", ["a", "b"], 42, 4, "v"),
            "content/timestamp/relevancy/session_id/source/tags/tokens/version/metadata all survive reload")

    def test_reload_recomputes_embeddings(self) -> None:
        # Embeddings are NOT stored; they recompute on load when an embedder is injected, so semantic
        # seeding works on a reloaded store. A recording embedder proves the reload re-embeds node content.
        class _Rec:
            def __init__(self) -> None:
                self.docs: list = []

            def __call__(self, text, *, input_type=None):
                if input_type == "document":
                    self.docs.append(text)
                return [1.0, 0.0] if text in ("qparrot", "kiwi") else [0.0, 1.0]

        GraphStore(path=self.path).write(_item("kiwi", "kiwi", [], ts=1.0))  # written WITHOUT an embedder
        rec = _Rec()
        g2 = GraphStore(path=self.path, embed=rec)                            # reloaded WITH one
        self.assertIn("kiwi", rec.docs, "reload re-embedded the node content as a document")
        # the recomputed embedding lets a lexically-disjoint but cosine-close query seed the node:
        self.assertIn("kiwi", _ids(g2.search("qparrot", k=5)),
                      "recomputed embedding -> semantic seed recovers the node after reload")

    def test_malformed_okf_links_write_is_atomic(self) -> None:
        # A JSON-serializable but malformed okf_links (not iterable) must be rejected BEFORE persisting:
        # edges are parsed before the durable write, so neither RAM nor disk is mutated (gate round 3).
        g = GraphStore(path=self.path)
        g.write(_item("ok", "ok", [], ts=1.0))
        with self.assertRaises(TypeError):
            g.write(MemoryItem(item_id="bad", content="bad", metadata={"okf_links": 1}))  # 1 is not iterable
        self.assertIsNone(g.get("bad"), "malformed-edge write must not appear in RAM")
        self.assertEqual({it.item_id for it in g.all()}, {"ok"}, "only the good node is present in RAM")
        g2 = GraphStore(path=self.path)
        self.assertEqual({it.item_id for it in g2.all()}, {"ok"},
                         "malformed-edge write must not have persisted (parsed before persist)")


if __name__ == "__main__":
    unittest.main()
