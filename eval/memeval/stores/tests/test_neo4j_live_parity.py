"""Opt-in LIVE Neo4j parity — the repeatable form of the captained parity run (GRAPH_STORE_SCOPE step 5).

Owner: Brent (@bgibson1618).

This is the ONE test that exercises ``Neo4jGraphStore`` against a REAL Neo4j 5.x instance over the actual
Bolt driver — closing the "runs under load is never executed" gap that the stdlib ``FakeBoltDriver`` parity
suite (``test_neo4j_parity.py``) cannot: the fake proves our Cypher *shapes* and the parity-by-construction
delegation; only a live DB proves the real driver round-trips those shapes faithfully.

**SKIPPED in stdlib CI.** It runs ONLY when ``NEO4J_TEST_URI`` is set (``@skipUnless``), so the offline /
zero-dependency path is untouched and ``neo4j`` is never imported in CI. Set ``NEO4J_TEST_URI`` (e.g.
``bolt://localhost:7687``) and optionally ``NEO4J_TEST_USER`` / ``NEO4J_TEST_PASSWORD`` to run it against a
throwaway local Neo4j 5.x:

    NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_TEST_USER=neo4j NEO4J_TEST_PASSWORD=… \\
        python3 -m unittest memeval.stores.tests.test_neo4j_live_parity

It writes the SAME parity CORPUS as ``test_neo4j_parity`` and asserts BYTE-IDENTICAL ordered id lists vs the
in-memory ``GraphStore`` baseline across the same queries as the fake suite's full-corpus case. ``tearDown``
DETACH-DELETEs every node it wrote, so the test is self-cleaning and safe to re-run.

**captained-validation-pending:** this test is written but has NOT yet been validated against a real Neo4j
instance (no live DB in this environment). It is the executable spec for the captained step-5 run — running
it green against a live 5.x is what flips that flag.
"""

from __future__ import annotations

import os
import unittest

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore

# Reuse the EXACT parity corpus + helpers from the fake-driver suite so live and offline assert the same
# fixture (a divergence between the two would be a fixture bug, not a parity result).
from memeval.stores.tests.test_neo4j_parity import CORPUS, _ids

_LIVE_URI = os.environ.get("NEO4J_TEST_URI")
_QUERIES = (
    "Zephyr dependency", "Zephyr dependents", "Hub conflict", "Hub dependency", "Hub callee",
    "Apex chain tail", "Solis related", "Nimbus related", "Quasar partitions", "Delta commits writes",
)


@unittest.skipUnless(_LIVE_URI, "set NEO4J_TEST_URI to run the live Neo4j parity test (needs a Neo4j 5.x)")
class LiveNeo4jParityTests(unittest.TestCase):
    """Byte-identical ids+order vs the in-memory baseline, against a real Neo4j over the Bolt driver."""

    MAX_DEPTH = 3  # reach the depth-3 multi_hop gold (matches the fake suite's full-corpus case)

    def setUp(self) -> None:
        from memeval.stores.neo4j_store import Neo4jGraphStore  # lazy: only when the test actually runs

        user = os.environ.get("NEO4J_TEST_USER")
        password = os.environ.get("NEO4J_TEST_PASSWORD")
        auth = (user, password) if user is not None else None
        self.store = Neo4jGraphStore(uri=_LIVE_URI, auth=auth, max_depth=self.MAX_DEPTH)
        # Clean any residue from a prior aborted run BEFORE seeding, so the assertion sees only our corpus.
        self._purge()
        for it in CORPUS:
            self.store.write(it)

        self.baseline = GraphStore(max_depth=self.MAX_DEPTH)
        for it in CORPUS:
            self.baseline.write(it)

    def tearDown(self) -> None:
        try:
            self._purge()
        finally:
            self.store.close()

    def _purge(self) -> None:
        """DETACH DELETE every corpus node (self-cleaning; idempotent — delete of an absent id is False)."""
        for it in CORPUS:
            self.store.delete(it.item_id)

    def test_live_byte_identical_ordered_ids(self) -> None:
        for query in _QUERIES:
            base_ids = _ids(self.baseline.search(query, k=5))
            live_ids = _ids(self.store.search(query, k=5))
            self.assertEqual(live_ids, base_ids,
                             f"live Neo4j parity broken for {query!r}: "
                             f"live={live_ids} != baseline={base_ids}")

    def test_live_crud_round_trip(self) -> None:
        # A direct write/get/all/delete round-trip over the real driver (the corpus is already seeded).
        self.assertIsInstance(self.store.get("td-zephyr"), MemoryItem)
        self.assertEqual({i.item_id for i in self.store.all()}, {it.item_id for it in CORPUS})
        self.assertTrue(self.store.delete("noise-indigo"))
        self.assertFalse(self.store.delete("noise-indigo"), "delete is idempotent on the live store")
        self.assertIsNone(self.store.get("noise-indigo"))


if __name__ == "__main__":
    unittest.main()
