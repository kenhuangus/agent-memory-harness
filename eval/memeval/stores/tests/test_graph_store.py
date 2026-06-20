"""Unit tests for :class:`memeval.stores.graph_store.GraphStore`. Owner: Brent.

v1 is stdlib-only and in-memory: memories are nodes, OKF links
(``metadata["okf_links"]``) are edges, and ``search`` finds the seed node(s) that
match the query then traverses the link neighborhood (BFS, bounded depth). These
tests center on the backend's distinctive value — a query matching one node also
surfaces its *linked* neighbors that don't match the query keywords — plus the
usual contract invariants. Real persistence + a typed-edge graph DB (Neo4j) are a
deferred paid-path seam.

Run from `eval/`:  python3 -m unittest memeval.stores.tests.test_graph_store
"""

from __future__ import annotations

import unittest

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore


def _mk(item_id: str, content: str, *, timestamp: float = 0.0, relevancy: float = 1.0,
        links=None, tags=None) -> MemoryItem:
    md = {"okf_links": list(links)} if links else {}
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy, tags=list(tags or []), metadata=md)


class GraphStoreTests(unittest.TestCase):
    def store(self) -> GraphStore:
        return GraphStore()

    def test_satisfies_memorystore_protocol(self) -> None:
        self.assertIsInstance(self.store(), MemoryStore)

    def test_write_get_round_trip(self) -> None:
        s = self.store()
        s.write(_mk("a", "auth module login"))
        got = s.get("a")
        self.assertIsNotNone(got)
        self.assertEqual(got.item_id, "a")
        self.assertIn("auth", got.content)
        self.assertGreater(got.tokens, 0)
        self.assertIsNone(s.get("missing"))

    def test_all_returns_written_items(self) -> None:
        s = self.store()
        s.write(_mk("a", "alpha"))
        s.write(_mk("b", "beta"))
        self.assertEqual(sorted(i.item_id for i in s.all()), ["a", "b"])

    def test_search_finds_seed_node(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module"))
        s.write(_mk("z", "unrelated banana recipe"))
        hits = s.search("authentication", k=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0].item_id, "a")
        self.assertEqual(hits[0].rank, 0)
        self.assertGreater(hits[0].tokens, 0)

    def test_traversal_surfaces_linked_neighbor(self) -> None:
        # the graph store's reason to exist: a query matching A also returns A's
        # linked neighbor B, even though B has none of the query keywords.
        s = self.store()
        s.write(_mk("a", "authentication module", links=["b"]))
        s.write(_mk("b", "session token store"))      # no 'authentication' keyword
        s.write(_mk("d", "banana smoothie recipe"))   # unrelated + unlinked
        ids = [h.item_id for h in s.search("authentication", k=5)]
        self.assertEqual(ids[0], "a")     # direct match first
        self.assertIn("b", ids)           # neighbor surfaced via the link
        self.assertNotIn("d", ids)        # unrelated + unreachable

    def test_neighbor_ranks_below_seed(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module", links=["b"]))
        s.write(_mk("b", "session token store"))
        score = {h.item_id: h.score for h in s.search("authentication", k=5)}
        self.assertGreater(score["a"], score["b"])

    def test_as_of_excludes_future_neighbor(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module", links=["b"], timestamp=100.0))
        s.write(_mk("b", "session token store", timestamp=200.0))
        ids = [h.item_id for h in s.search("authentication", k=5, as_of=150.0)]
        self.assertIn("a", ids)
        self.assertNotIn("b", ids)

    def test_no_seed_match_returns_empty(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module"))
        self.assertEqual(s.search("xylophone", k=5), [])
        self.assertEqual(s.search("", k=5), [])

    def test_overwrite_updates_edges(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module", links=["b"]))
        s.write(_mk("b", "session token store"))
        s.write(_mk("c", "config service"))
        s.write(_mk("a", "authentication module", links=["c"]))  # re-link a -> c
        ids = [h.item_id for h in s.search("authentication", k=5)]
        self.assertIn("c", ids)
        self.assertNotIn("b", ids)

    def test_write_does_not_mutate_caller_tokens(self) -> None:
        s = self.store()
        item = _mk("m1", "authentication module")  # tokens defaults to 0
        s.write(item)
        self.assertEqual(item.tokens, 0)            # caller's object untouched
        self.assertGreater(s.get("m1").tokens, 0)   # stored copy has tokens populated

    def test_traversal_respects_max_depth(self) -> None:
        # chain a->b->c->d; only a matches. With MAX_DEPTH=2, d (distance 3) is excluded.
        s = self.store()
        s.write(_mk("a", "authentication module", links=["b"]))
        s.write(_mk("b", "node bee", links=["c"]))
        s.write(_mk("c", "node cee", links=["d"]))
        s.write(_mk("d", "node dee"))
        ids = {h.item_id for h in s.search("authentication", k=10)}
        self.assertEqual(ids, {"a", "b", "c"})  # d at distance 3 excluded

    def test_traverses_in_edges(self) -> None:
        # b links TO a (an in-edge of a); a query matching a still surfaces b.
        s = self.store()
        s.write(_mk("a", "authentication module"))
        s.write(_mk("b", "session token store", links=["a"]))
        self.assertIn("b", [h.item_id for h in s.search("authentication", k=5)])

    def test_dangling_link_does_not_crash(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module", links=["nonexistent"]))
        self.assertEqual([h.item_id for h in s.search("authentication", k=5)], ["a"])

    def test_resolves_path_form_links(self) -> None:
        # OKF emits link targets as paths; v1 maps basename-minus-.md to an item_id.
        s = self.store()
        s.write(_mk("a", "authentication module", links=["/memory/b.md"]))
        s.write(_mk("b", "session token store"))
        self.assertIn("b", [h.item_id for h in s.search("authentication", k=5)])

    def test_k_zero_returns_empty(self) -> None:
        s = self.store()
        s.write(_mk("a", "authentication module"))
        self.assertEqual(s.search("authentication", k=0), [])


if __name__ == "__main__":
    unittest.main()
