"""Delete eval — `delete(item_id)` across the 3 backends + Router/RouterStore fan-out (PR-B).

Owner: Brent. Eval-first: written before the delete mechanism.

Nothing had a delete (the frozen `MemoryStore` protocol is write/get/search/all). This adds a
**solo-additive, duck-typed** delete (NOT a `[CONTRACT]` change — putting `delete` on the protocol is the
follow-up): each backend removes the item (durably, where it has a file), and `Router`/`RouterStore` fan
delete out to EVERY registered backend — delete is **unconditional/complete** (base_all writes an item to
all 3, so delete must clear all 3), and **idempotent** (a missing id is a no-op).

ANTI-THEATER: durable deletes are verified by reloading a SEPARATE store instance from disk and asserting
the item is gone — a pass means it left the FILE, not just RAM. The graph delete also retracts its edges
while preserving the `_out`<->`_in` mirror (a source's edge to a deleted target is the source's data and
survives; it simply resolves to nothing until the target returns).

Run from `eval/`:  python3 -m unittest memeval.stores.tests.test_delete_evals
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


def _ids(hits) -> list:
    return [h.item_id for h in hits]


class BackendDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sqlite_delete_durable_and_idempotent(self) -> None:
        p = os.path.join(self.dir, "v.db")
        s = SqliteVectorStore(p)
        s.write(_item("a", "alpha"))
        s.write(_item("b", "bravo"))
        self.assertTrue(s.delete("a"))
        self.assertIsNone(s.get("a"))
        self.assertFalse(s.delete("a"), "idempotent: a second delete removes nothing -> False")
        self.assertFalse(s.delete("missing"), "deleting an absent id is a no-op -> False")
        s.close()
        s2 = SqliteVectorStore(p)  # fresh instance, same file
        self.assertIsNone(s2.get("a"), "deleted row is gone from disk")
        self.assertIsNotNone(s2.get("b"), "the other row survived")
        s2.close()

    def test_markdown_delete_durable_and_deindexes(self) -> None:
        d = os.path.join(self.dir, "md")
        m = MarkdownStore(d)
        m.write(_item("a", "alpha keyword"))
        m.write(_item("b", "bravo keyword"))
        self.assertTrue(m.delete("a"))
        self.assertIsNone(m.get("a"))
        self.assertNotIn("a", _ids(m.search("alpha", k=5)), "de-indexed -> not a keyword candidate")
        self.assertFalse(m.delete("a"), "idempotent")
        m2 = MarkdownStore(d)  # fresh store autoloads the bundle from disk
        self.assertIsNone(m2.get("a"), "doc unlinked -> not reloaded from the bundle")
        self.assertIsNotNone(m2.get("b"))

    def test_graph_delete_durable_and_retracts_edges(self) -> None:
        p = os.path.join(self.dir, "g.db")
        g = GraphStore(path=p)
        g.write(_item("alpha", "alpha", [["depends on", "beta"]], ts=1.0))
        g.write(_item("beta", "beta", [], ts=2.0))
        self.assertIn("alpha", _ids(g.search("what depends on beta", k=5)),
                      "before delete: alpha is beta's depends_on in-edge")
        self.assertTrue(g.delete("alpha"))
        self.assertIsNone(g.get("alpha"))
        self.assertNotIn("alpha", _ids(g.search("what depends on beta", k=5)),
                         "edge retracted: alpha no longer reachable as beta's in-edge")
        self.assertFalse(g.delete("alpha"), "idempotent")
        g.close()
        g2 = GraphStore(path=p)  # fresh instance from disk
        self.assertIsNone(g2.get("alpha"), "deleted node gone from disk")
        self.assertIsNotNone(g2.get("beta"))
        g2.close()

    def test_graph_delete_on_closed_store_fails_loud(self) -> None:
        p = os.path.join(self.dir, "g2.db")
        g = GraphStore(path=p)
        g.write(_item("a", "a"))
        g.close()
        with self.assertRaises(RuntimeError):
            g.delete("a")


class GraphDeleteIndexTests(unittest.TestCase):
    # In-memory only (no tempfile), so this index-invariant test runs anywhere — incl. a no-/tmp sandbox.
    def test_graph_delete_preserves_out_in_mirror(self) -> None:
        # Deleting a TARGET leaves the source's edge intact (its data); the edge just resolves to nothing
        # until the target returns. This proves _out/_in stay mutually consistent across a delete.
        g = GraphStore()  # in-memory; no path needed
        g.write(_item("alpha", "alpha", [["depends on", "beta"]], ts=1.0))
        g.write(_item("beta", "beta", [], ts=2.0))
        g.delete("beta")  # delete the TARGET
        self.assertIsNotNone(g.get("alpha"), "the source node survives")
        self.assertNotIn("beta", _ids(g.search("alpha", k=5)), "dangling edge to a deleted target is filtered")
        g.write(_item("beta", "beta", [], ts=3.0))  # re-create the target
        self.assertIn("beta", _ids(g.search("alpha", k=5)),
                      "source out-edge preserved -> recreating the target makes it reachable again")
        # AND the reverse index survived: an IN-query proves _in[beta] kept alpha's depends_on edge across
        # delete + recreate (this would fail if delete had dropped _in[beta]).
        self.assertIn("alpha", _ids(g.search("what depends on beta", k=5)),
                      "_in[beta] preserved alpha's depends_on edge across delete + recreate")


class RouterDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.backends = {
            MARKDOWN: MarkdownStore(os.path.join(d, "md")),
            VECTORS: SqliteVectorStore(os.path.join(d, "v.db")),
            GRAPH: GraphStore(path=os.path.join(d, "g.db")),
        }
        self.router = Router.with_config(backends=self.backends, config=RouterConfig())
        self.rs = RouterStore(self.router)  # RouterStore.delete -> bool; self.router.delete -> count

    def tearDown(self) -> None:
        self.backends[VECTORS].close()
        self.backends[GRAPH].close()
        self._tmp.cleanup()

    def test_delete_fans_out_to_all_backends(self) -> None:
        self.rs.write(_item("x", "xray content"))  # base_all -> x lands in all 3
        for name in (MARKDOWN, VECTORS, GRAPH):
            self.assertIsNotNone(self.backends[name].get("x"), f"{name} should have x after write")
        self.assertTrue(self.rs.delete("x"), "RouterStore.delete -> True when present")
        for name in (MARKDOWN, VECTORS, GRAPH):
            self.assertIsNone(self.backends[name].get("x"), f"{name} should NOT have x after delete")
        self.assertIsNone(self.rs.get("x"))
        self.assertNotIn("x", [i.item_id for i in self.rs.all()])

    def test_delete_is_idempotent(self) -> None:
        self.rs.write(_item("x", "xray content"))
        self.assertTrue(self.rs.delete("x"))
        self.assertFalse(self.rs.delete("x"), "a second delete removes from nothing -> False")
        self.assertFalse(self.rs.delete("never"), "absent id -> False")

    def test_router_delete_count_vs_routerstore_bool(self) -> None:
        # Router.delete returns the per-backend COUNT (it is NOT a MemoryStore); RouterStore.delete (the
        # protocol facade) collapses it to a bool. Written to ONLY the graph backend -> count 1.
        self.backends[GRAPH].write(_item("solo", "solo"))
        self.assertEqual(self.router.delete("solo"), 1, "Router.delete: only the graph backend had it")
        self.assertEqual(self.router.delete("solo"), 0, "Router.delete: idempotent count -> 0")
        self.backends[GRAPH].write(_item("solo2", "solo2"))
        self.assertIs(self.rs.delete("solo2"), True, "RouterStore.delete: bool True when present")
        self.assertIs(self.rs.delete("solo2"), False, "RouterStore.delete: bool False when absent")


class ReferenceStoreDeleteTests(unittest.TestCase):
    # The frozen MemoryStore protocol now declares delete; the reference InMemoryStore implements it
    # (in-memory only, no tempfile -> runs anywhere).
    def test_inmemory_store_delete(self) -> None:
        from memeval.harness import InMemoryStore
        s = InMemoryStore()
        s.write(_item("a", "alpha"))
        s.write(_item("b", "bravo"))
        self.assertTrue(s.delete("a"))
        self.assertIsNone(s.get("a"))
        self.assertEqual({i.item_id for i in s.all()}, {"b"}, "delete drops it from all()/insertion order")
        self.assertFalse(s.delete("a"), "idempotent: second delete -> False")
        self.assertFalse(s.delete("missing"), "absent id -> False")


if __name__ == "__main__":
    unittest.main()
