"""Offline Falkor native typed-graph mechanism tests.

These tests run entirely against ``test_falkor_parity``'s stdlib fake. They pin the PR2 native
wire shapes and invariants without requiring FalkorDB or the ``falkordb`` package.
"""

from __future__ import annotations

import unittest

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.tests.test_falkor_parity import CORPUS, FakeFalkorClient, _ids, _item


def _FalkorGraphStore():
    from memeval.stores.falkor_store import FalkorGraphStore
    return FalkorGraphStore


def _native_store(*, max_depth: int = 2):
    client = FakeFalkorClient()
    store = _FalkorGraphStore()(client=client, max_depth=max_depth, native=True)
    return store, client


class NativeMaterializeTests(unittest.TestCase):
    def test_materialize_uses_double_match_merge_and_no_target_merge(self) -> None:
        store, client = _native_store()
        store.write(_item("a", "A anchors a target.", [["depends on", "b"]], ts=1.0))
        client.calls.clear()

        store.materialize()

        cyphers = [c for c, _p in client.calls]
        merge = [c for c in cyphers if "UNWIND $edges AS e" in c]
        self.assertEqual(len(merge), 1)
        self.assertIn("MATCH (a:Memory {item_id: e.src})", merge[0])
        self.assertIn("MATCH (b:Memory {item_id: e.tgt})", merge[0])
        self.assertIn("MERGE (a)-[r:REL {rel_type: e.rel}]->(b)", merge[0])
        self.assertNotIn("MERGE (b:Memory", merge[0],
                         "materialize must MATCH, never MERGE, a target endpoint")
        self.assertEqual(client.rels, set(), "dangling target binds no b row -> no rel")
        self.assertNotIn("b", client.nodes, "dangling target must not become a placeholder node")

    def test_forward_reference_resolves_after_both_nodes_exist(self) -> None:
        store, client = _native_store()
        store.write(_item("a", "A anchors a target.", [["depends on", "b"]], ts=1.0))
        store.materialize()
        self.assertEqual(client.rels, set())

        store.write(_item("b", "B is now real.", ts=2.0))
        store.materialize()

        self.assertEqual(client.rels, {("a", "b", "depends_on")})


class NativeSearchShapeTests(unittest.TestCase):
    def test_native_search_emits_variable_length_traversal_with_as_of_and_seeds(self) -> None:
        store, client = _native_store(max_depth=3)
        store.write(_item("s", "Zenith schedules work.", [["depends on", "a"]], ts=1.0))
        store.write(_item("a", "Apogee handles work.", ts=2.0))
        client.calls.clear()

        hits = store.search("Zenith dependency", k=3, as_of=5.0)

        self.assertEqual(_ids(hits), ["a"])
        traversals = [(c, p) for c, p in client.calls if "MATCH p=(s:Memory)" in c]
        self.assertEqual(len(traversals), 1)
        cypher, params = traversals[0]
        self.assertIn("[:REL*1..3]->(m:Memory)", cypher)
        self.assertIn("relationships(p)", cypher)
        self.assertIn("nodes(p)", cypher)
        self.assertIn("$as_of", cypher)
        self.assertEqual(params["as_of"], 5.0)
        self.assertEqual(params["seed_ids"], ["s"])
        self.assertEqual(params["rel"], "depends_on")

    def test_native_search_sorts_with_baseline_tie_break_tuple(self) -> None:
        store, _client = _native_store()
        store.write(_item("s", "Zenith schedules work.",
                          [["depends on", "a"], ["depends on", "b"]], ts=1.0))
        store.write(MemoryItem(item_id="a", content="Apogee handles work.", timestamp=2.0,
                               relevancy=0.4, metadata={"okf_title": "a"}))
        store.write(MemoryItem(item_id="b", content="Borealis handles work.", timestamp=3.0,
                               relevancy=0.9, metadata={"okf_title": "b"}))

        self.assertEqual(_ids(store.search("Zenith dependency", k=2)), ["b", "a"])


class NativeNoRegressionTests(unittest.TestCase):
    def test_native_id_set_no_regression_on_parity_corpus(self) -> None:
        store, _client = _native_store(max_depth=3)
        baseline = GraphStore(max_depth=3)
        for item in CORPUS:
            store.write(item)
            baseline.write(item)

        cases = (
            ("Zephyr dependency", "td-quasar", "td-vortex"),
            ("Zephyr dependents", "td-vortex", "td-quasar"),
            ("Hub conflict", "rd-beta", "rd-alpha"),
            ("Hub dependency", "rd-alpha", "rd-beta"),
            ("Hub callee", "rd-gamma", "rd-alpha"),
            ("Solis related", "uf-luna", "uf-stratus"),
            ("Nimbus related", "uf-stratus", "uf-luna"),
        )
        for query, gold, distractor in cases:
            with self.subTest(query=query):
                base_ids = set(_ids(baseline.search(query, k=5)))
                native_ids = set(_ids(store.search(query, k=5)))
                self.assertIn(gold, base_ids)
                self.assertNotIn(distractor, base_ids)
                self.assertIn(gold, native_ids)
                self.assertNotIn(distractor, native_ids)


if __name__ == "__main__":
    unittest.main()
