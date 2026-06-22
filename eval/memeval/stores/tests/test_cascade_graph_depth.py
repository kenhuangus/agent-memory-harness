"""Cascade graph-depth wiring eval — the accuracy profile traverses the graph DEEPER (D032 follow-up).

Owner: Brent. Eval-first: written before the wiring it gates.

#85 made ``GraphStore`` BFS depth configurable (``GraphStore(max_depth=)``), but only the eval set it —
the production cascade / accuracy profile still traversed depth-2. This wires the depth through end to end:

* ``GraphStore.search`` honors a **per-call** ``max_depth`` (the cascade injects it per query),
* ``CascadeConfig.graph_max_depth`` carries the profile's depth (``None`` = the store's own default),
* ``accuracy_profile()`` sets it deeper, and ``_GraphVectorCascade`` injects it into its graph stage.

The proof is end-to-end through the cascade's ACCEPT path: a backticked-anchor query on a chain HEAD
gate-ACCEPTs (unique exact anchor at graph rank-0), and ``_project`` returns the traversed graph hits.
With the speed/default cascade (``graph_max_depth=None`` -> depth 2) the depth-3 chain tail is unreachable
and absent from the result; with the accuracy cascade (``graph_max_depth=3``) it is RECOVERED. The chain
tail is a coined/inert token reachable ONLY via links, so the difference is traversal depth, not lexical
seeding (the link-stripped control proves it).

RED before ``GraphStore.search`` honored the per-call ``max_depth`` (the cascade injected it but the store
swallowed the kwarg -> depth 2 -> tail missed); GREEN after.

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_cascade_graph_depth
"""

from __future__ import annotations

import unittest

from memeval.router import (
    ACCEPT,
    CascadeConfig,
    _GraphVectorCascade,
    accuracy_profile,
    speed_profile,
)
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.sqlite_store import SqliteVectorStore

# A 4-node "calls" chain whose HEAD is lexically seedable + backtick-anchorable, and whose tail (the
# depth-3 gold) is a coined/inert token reachable ONLY by traversing the chain:
#   router_core --calls--> rc_bravo --calls--> rc_charlie --calls--> rc_delta (depth 3)
_CHAIN = [
    ("router_core", "router_core dispatches queries.", [["calls", "rc_bravo"]]),
    ("rc_bravo", "Bravo validates payloads.", [["calls", "rc_charlie"]]),
    ("rc_charlie", "Charlie reserves stock.", [["calls", "rc_delta"]]),
    ("rc_delta", "Delta commits writes.", []),
    # inert noise (coined, link-free, disjoint tokens) so nothing else seeds and k stays selective
    ("noise_onyx", "Onyx throttles producers.", []),
    ("noise_jade", "Jade encrypts volumes.", []),
]

_HEAD = "router_core"
_DEEP_GOLD = "rc_delta"                 # depth 3 from the head
_QUERY = "`router_core` call chain"     # backticked anchor on the head + seeds it; the gate ACCEPTs


class _DummyClassifier:
    """Minimal RouterClassifier stand-in — accuracy_profile only stores it in the config."""

    def classify(self, query, **kwargs):
        return "vectors"


def _item(iid: str, content: str, links: list) -> MemoryItem:
    return MemoryItem(item_id=iid, content=content,
                      metadata={"okf_title": iid, "okf_links": links})


def _build_stores(*, strip_links: bool = False):
    graph = GraphStore()
    vector = SqliteVectorStore()  # :memory:, offline hashing embedder
    for iid, content, links in _CHAIN:
        it = _item(iid, content, [] if strip_links else links)
        graph.write(it)
        vector.write(it)
    return graph, vector


def _cascade(graph_max_depth, *, strip_links: bool = False) -> _GraphVectorCascade:
    graph, vector = _build_stores(strip_links=strip_links)
    return _GraphVectorCascade(graph, vector,
                               CascadeConfig(enabled=True, graph_max_depth=graph_max_depth))


def _ids(hits) -> list:
    return [h.item_id for h in hits]


class CascadeGraphDepthTests(unittest.TestCase):
    def test_gate_accepts_in_both_profiles(self) -> None:
        # The difference must be DEPTH, not the gate verdict: the backticked-anchor query ACCEPTs in both
        # the shallow and the deep cascade (same anchor, same rank-0 head). GREEN before and after.
        for depth in (None, 3):
            verdict = _cascade(depth).gate(_QUERY, k=5)
            self.assertEqual(verdict.decision, ACCEPT,
                             f"graph_max_depth={depth}: gate must ACCEPT (anchor=head, rank-0)")

    def test_speed_default_misses_depth3_gold(self) -> None:
        # Speed/default cascade: graph_max_depth=None -> the graph store's own default depth (2) -> the
        # depth-3 chain tail is NOT traversed -> absent from the cascade output. GREEN before and after.
        hits = _ids(_cascade(None).search(_QUERY, k=5))
        self.assertIn(_HEAD, hits, "head must be retrieved (gate accepted)")
        self.assertNotIn(_DEEP_GOLD, hits,
                         "depth-2 cascade must MISS the depth-3 gold (the preserved reach headroom)")

    def test_accuracy_depth_recovers_depth3_gold(self) -> None:
        # THE polarity carrier. Accuracy cascade: graph_max_depth=3 -> the cascade injects max_depth=3 into
        # graph.search -> the depth-3 chain tail is traversed -> present in the cascade output. RED until
        # GraphStore.search honors the per-call max_depth (before that it swallowed the kwarg -> depth 2).
        hits = _ids(_cascade(3).search(_QUERY, k=5))
        self.assertIn(_DEEP_GOLD, hits,
                      "accuracy cascade (graph_max_depth=3) must RECOVER the depth-3 gold")

    def test_recovery_is_traversal_not_lexical(self) -> None:
        # Anti-theater: strip the links and even the deep cascade cannot reach the gold (a coined,
        # link-only token) -> the recovery is graph TRAVERSAL, not lexical seeding.
        hits = _ids(_cascade(3, strip_links=True).search(_QUERY, k=5))
        self.assertNotIn(_DEEP_GOLD, hits,
                         "no links -> deep gold unreachable even at depth 3 (else lexical, not traversal)")

    def test_profiles_wire_graph_max_depth(self) -> None:
        # The factory wires the depth: accuracy_profile carries a deeper graph_max_depth into its
        # CascadeConfig; speed leaves it None (byte-equivalent default). GREEN before and after.
        acc = accuracy_profile(classifier=_DummyClassifier(), embed=object())
        self.assertEqual(acc.cascade.graph_max_depth, 3,
                         "accuracy_profile must set a deeper cascade graph_max_depth")
        self.assertIsNone(speed_profile().cascade.graph_max_depth,
                          "speed_profile must leave graph_max_depth None (byte-equivalent)")


if __name__ == "__main__":
    unittest.main()
