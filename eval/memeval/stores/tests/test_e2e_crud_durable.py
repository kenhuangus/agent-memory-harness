"""End-to-end CRUD eval — the full system (RouterStore over all 3 DURABLE backends) survives a restart.

Owner: Brent. The capstone deliverable: "an e2e test of the system across all 3 backends." Now that every
backend persists (markdown -> OKF bundle dir, vectors -> memory.db, graph -> graph.db via the `path=` seam,
#92) and delete exists (#93), this exercises the complete lifecycle through the REAL `RouterStore` + `Router`
+ all three concrete backends: **Create -> Read -> Update -> Delete -> restart**, reconstructing every
backend from disk in fresh instances and confirming the persisted state.

ANTI-THEATER: the post-restart assertions run against a SEPARATE `RouterStore` built over NEW backend
instances pointed at the same paths (the writers are closed first) — so a pass proves the data lives on
DISK across all three backends, not in any in-RAM store. Each backend is ALSO checked INDEPENDENTLY (not
only the router's union) so one durable backend can't mask a non-durable one.

Scope: exercises Brent's data layer (stores + router) end-to-end. The plugin's `_Engine`/`build_store`
wrapper builds the graph WITHOUT a path today (in-memory), so making the LIVE plugin graph durable is the
cross-team `build_store` wiring follow-up (Keith) — this test proves the capability that wiring turns on.

Run from `eval/`:  python3 -m unittest memeval.stores.tests.test_e2e_crud_durable
"""

from __future__ import annotations

import os
import tempfile
import unittest

from memeval.router import GRAPH, MARKDOWN, VECTORS, Router, RouterConfig, RouterStore
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore


def _item(iid: str, content: str, links=None, ts: float = 0.0) -> MemoryItem:
    md = {"okf_title": iid}
    if links is not None:
        md["okf_links"] = links
    return MemoryItem(item_id=iid, content=content, timestamp=ts, metadata=md)


class E2ECrudDurableTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _open(self):
        """A RouterStore over the 3 DURABLE backends at fixed paths (FRESH instances each call -> a real
        cold reload from disk on the second call)."""
        backends = {
            MARKDOWN: MarkdownStore(os.path.join(self.d, "md")),
            VECTORS: SqliteVectorStore(os.path.join(self.d, "v.db")),
            GRAPH: GraphStore(path=os.path.join(self.d, "g.db")),
        }
        return RouterStore(Router.with_config(backends=backends, config=RouterConfig())), backends

    @staticmethod
    def _close(backends) -> None:
        backends[VECTORS].close()
        backends[GRAPH].close()  # markdown/OKF persists per-write; no handle to close

    def test_full_crud_lifecycle_survives_restart(self) -> None:
        # ---- CREATE (base_all -> each item lands in all 3 backends) ----
        rs, b1 = self._open()
        rs.write(_item("apple", "apple fruit red", ts=1.0))
        rs.write(_item("banana", "banana fruit yellow", ts=2.0))
        rs.write(_item("cherry", "cherry fruit red", ts=3.0))
        for iid in ("apple", "banana", "cherry"):
            for name in (MARKDOWN, VECTORS, GRAPH):
                self.assertIsNotNone(b1[name].get(iid), f"{iid} should be in {name} after create")
        # ---- READ (routed search + union get/all) ----
        self.assertIsNotNone(rs.get("apple"))
        self.assertTrue(any(h.item_id == "apple" for h in rs.search("apple fruit red", k=5)),
                        "routed search finds a created item")
        self.assertEqual({i.item_id for i in rs.all()}, {"apple", "banana", "cherry"})
        # ---- UPDATE (newer content wins; base_all rewrites every backend) ----
        rs.write(_item("banana", "banana fruit GREEN now", ts=4.0))
        self.assertIn("GREEN", rs.get("banana").content)
        # ---- DELETE (fan-out to all 3) ----
        self.assertEqual(rs.delete("cherry"), 3, "cherry removed from all 3 backends")
        self.assertIsNone(rs.get("cherry"))
        self._close(b1)  # close the writers -> force a genuine reload from disk

        # ---- RESTART: reconstruct EVERY backend from disk in fresh instances ----
        rs2, b2 = self._open()
        self.assertEqual({i.item_id for i in rs2.all()}, {"apple", "banana"},
                         "survivors persisted; the deleted item did not come back")
        self.assertIn("GREEN", rs2.get("banana").content, "the update persisted across restart")
        self.assertIsNone(rs2.get("cherry"), "the delete persisted across restart")
        # per-backend INDEPENDENT durability — assert the survivors, the UPDATE content, AND the delete in
        # EACH backend (RouterStore.get reads markdown-first, so a per-backend check is what proves vectors
        # and graph also persisted the *updated* content, not just the id).
        for name in (MARKDOWN, VECTORS, GRAPH):
            self.assertEqual({i.item_id for i in b2[name].all()}, {"apple", "banana"},
                             f"{name} persisted the survivors + the delete across restart")
            self.assertIsNotNone(b2[name].get("apple"), f"{name} persisted apple")
            banana = b2[name].get("banana")
            self.assertIsNotNone(banana, f"{name} persisted banana")
            self.assertIn("GREEN", banana.content, f"{name} persisted the UPDATE, not the stale content")
            self.assertIsNone(b2[name].get("cherry"), f"{name} did not resurrect the deleted item")
        self._close(b2)

    def test_graph_relation_round_trips_after_restart(self) -> None:
        # A typed graph relation written before restart is rebuilt from okf_links on cold reload, so a
        # directional query still traverses it.
        rs, b1 = self._open()
        rs.write(_item("graphy", "graphy depends on widget", [["depends on", "widget"]], ts=1.0))
        rs.write(_item("widget", "widget component", ts=2.0))
        self._close(b1)
        rs2, b2 = self._open()
        self.assertIsNotNone(rs2.get("graphy"), "node persisted across restart")
        self.assertIsNotNone(rs2.get("widget"))
        self.assertIn("widget", [h.item_id for h in b2[GRAPH].search("what does graphy depend on", k=5)],
                      "typed depends_on edge rebuilt from disk -> widget reachable via traversal (not seeded)")
        self._close(b2)


if __name__ == "__main__":
    unittest.main()
