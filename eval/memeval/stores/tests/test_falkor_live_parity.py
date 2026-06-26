"""Opt-in LIVE FalkorDB parity — skipped unless ``FALKORDB_TEST_URI`` is set.

This is a PR2 hook only for proving the PR1 round-trip against a real FalkorDB instance. Offline CI imports
this file cleanly and skips the class; no ``falkordb`` import happens at collection time.
"""

from __future__ import annotations

import os
import unittest
import uuid

from memeval.stores.graph_store import GraphStore
from memeval.stores.tests.test_falkor_parity import CORPUS, _ids

_LIVE_URI = os.environ.get("FALKORDB_TEST_URI")
_QUERIES = (
    "Zephyr dependency", "Zephyr dependents", "Hub conflict", "Hub dependency", "Hub callee",
    "Apex chain tail", "Solis related", "Nimbus related", "Quasar partitions", "Delta commits writes",
)


@unittest.skipUnless(_LIVE_URI, "set FALKORDB_TEST_URI to run live parity")
class LiveFalkorParityTests(unittest.TestCase):
    """Byte-identical ids+order vs the in-memory baseline, against a real FalkorDB graph."""

    MAX_DEPTH = 3

    def setUp(self) -> None:
        from memeval.stores.falkor_store import FalkorGraphStore  # lazy: only when the live test runs

        self.graph_name = f"memeval_falkor_live_{uuid.uuid4().hex}"
        self.store = FalkorGraphStore(url=_LIVE_URI, graph_name=self.graph_name, max_depth=self.MAX_DEPTH)
        for it in CORPUS:
            self.store.write(it)

        self.baseline = GraphStore(max_depth=self.MAX_DEPTH)
        for it in CORPUS:
            self.baseline.write(it)

    def tearDown(self) -> None:
        try:
            db = getattr(self.store, "_db", None)
            if db is not None:
                graph = db.select_graph(self.graph_name)
                if hasattr(graph, "delete"):
                    graph.delete()
                else:
                    for it in CORPUS:
                        self.store.delete(it.item_id)
        finally:
            self.store.close()

    def test_live_byte_identical_ordered_ids(self) -> None:
        for query in _QUERIES:
            base_ids = _ids(self.baseline.search(query, k=5))
            live_ids = _ids(self.store.search(query, k=5))
            self.assertEqual(live_ids, base_ids,
                             f"live FalkorDB parity broken for {query!r}: "
                             f"live={live_ids} != baseline={base_ids}")

    def test_live_crud_round_trip(self) -> None:
        self.assertIsNotNone(self.store.get("td-zephyr"))
        self.assertEqual({i.item_id for i in self.store.all()}, {it.item_id for it in CORPUS})
        self.assertTrue(self.store.delete("noise-indigo"))
        self.assertFalse(self.store.delete("noise-indigo"), "delete is idempotent on the live store")
        self.assertIsNone(self.store.get("noise-indigo"))


if __name__ == "__main__":
    unittest.main()
