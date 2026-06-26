"""Falkor native typed-graph divergence eval.

The offline half proves deterministic headroom: the in-memory ``GraphStore`` baseline misses a coined
convergent-hub case at k=1 because it keeps only the single best flat-decay path. The native assertions
need a real FalkorDB and are double-gated by ``FALKORDB_TEST_URI`` and ``MEMEVAL_LIVE=1``.
"""

from __future__ import annotations

import os
import unittest
import uuid
from dataclasses import dataclass

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.tests.test_falkor_parity import _ids, _item

DROP_REASONS = ("baseline_solved",)


@dataclass(frozen=True)
class DivergenceCase:
    name: str
    query: str
    gold: str
    distractor: str
    items: tuple[MemoryItem, ...]
    k: int = 1
    max_depth: int = 2


def _convergent_hub_items(*, strip_convergent_edges: bool = False) -> tuple[MemoryItem, ...]:
    x_links = [] if strip_convergent_edges else [["depends on", "gold-apogee"]]
    y_links = [] if strip_convergent_edges else [["depends on", "gold-apogee"]]
    z_links = [] if strip_convergent_edges else [["depends on", "gold-apogee"]]
    return (
        _item("seed-zenith", "Zenith coordinates flows.",
              [["depends on", "relay-iolite"], ["depends on", "relay-juno"],
               ["depends on", "relay-kappa"], ["depends on", "relay-lumen"]], ts=1.0),
        _item("relay-iolite", "Iolite relays packets.", x_links, ts=2.0),
        _item("relay-juno", "Juno relays packets.", y_links, ts=3.0),
        _item("relay-kappa", "Kappa relays packets.", z_links, ts=4.0),
        _item("relay-lumen", "Lumen relays packets.", [["depends on", "distractor-borealis"]], ts=5.0),
        _item("gold-apogee", "Apogee reconciles ledgers.", ts=10.0),
        _item("distractor-borealis", "Borealis reconciles ledgers.", ts=20.0),
    )


def _baseline_solved_items() -> tuple[MemoryItem, ...]:
    return (
        _item("solved-gold", "Orchid beacon aligns.", ts=1.0),
        _item("solved-noise", "Cobalt relay idles.", ts=2.0),
    )


CONVERGENT_HUB = DivergenceCase(
    name="convergent_hub",
    query="Zenith dependency",
    gold="gold-apogee",
    distractor="distractor-borealis",
    items=_convergent_hub_items(),
)

CALIBRATION_CANDIDATES = (
    CONVERGENT_HUB,
    DivergenceCase(
        name="baseline_solved_lexical",
        query="Orchid",
        gold="solved-gold",
        distractor="solved-noise",
        items=_baseline_solved_items(),
    ),
)


def _baseline_ids(case: DivergenceCase, *, k: int | None = None) -> list[str]:
    store = GraphStore(max_depth=case.max_depth)
    for item in case.items:
        store.write(item)
    return _ids(store.search(case.query, k=case.k if k is None else k))


def _calibrate(cases: tuple[DivergenceCase, ...]) -> dict:
    retained = []
    dropped = []
    for case in cases:
        ids = _baseline_ids(case)
        if case.gold in ids:
            dropped.append({"name": case.name, "reason": "baseline_solved", "ids": ids})
        else:
            retained.append({"name": case.name, "ids": ids})
    return {
        "drop_reasons": list(DROP_REASONS),
        "retained": retained,
        "dropped": dropped,
    }


class OfflineHeadroomTests(unittest.TestCase):
    def test_in_memory_baseline_misses_convergent_hub_gold_at_k1(self) -> None:
        top1 = _baseline_ids(CONVERGENT_HUB)
        self.assertNotIn(CONVERGENT_HUB.gold, top1)

        full = _baseline_ids(CONVERGENT_HUB, k=10)
        self.assertIn(CONVERGENT_HUB.gold, full)
        self.assertIn(CONVERGENT_HUB.distractor, full)
        self.assertLess(full.index(CONVERGENT_HUB.distractor), full.index(CONVERGENT_HUB.gold),
                        "flat single-best-path scoring ties gold/distractor, then timestamp favors distractor")

    def test_calibration_drop_manifest_keeps_only_baseline_misses(self) -> None:
        manifest = _calibrate(CALIBRATION_CANDIDATES)
        self.assertEqual(manifest["drop_reasons"], ["baseline_solved"])
        self.assertEqual([row["name"] for row in manifest["retained"]], ["convergent_hub"])
        self.assertEqual([row["name"] for row in manifest["dropped"]], ["baseline_solved_lexical"])
        self.assertEqual(manifest["dropped"][0]["reason"], "baseline_solved")


_LIVE = bool(os.environ.get("FALKORDB_TEST_URI") and os.environ.get("MEMEVAL_LIVE") == "1")


@unittest.skipUnless(_LIVE, "set MEMEVAL_LIVE=1 + FALKORDB_TEST_URI to run live Falkor native eval")
class LiveNativeEvalTests(unittest.TestCase):
    def _live_store(self):
        from memeval.stores.falkor_store import FalkorGraphStore

        return FalkorGraphStore(url=os.environ["FALKORDB_TEST_URI"],
                                graph_name=f"falkor_native_eval_{uuid.uuid4().hex}",
                                native=True, max_depth=CONVERGENT_HUB.max_depth)

    def _load(self, items: tuple[MemoryItem, ...]):
        store = self._live_store()
        for item in items:
            store.write(item)
        return store

    def test_native_solves_convergent_hub_at_k1(self) -> None:
        store = self._load(CONVERGENT_HUB.items)
        try:
            self.assertEqual(_ids(store.search(CONVERGENT_HUB.query, k=1)), [CONVERGENT_HUB.gold])
        finally:
            store.close()

    def test_native_win_is_link_differential(self) -> None:
        stripped = DivergenceCase(
            name="convergent_hub_stripped",
            query=CONVERGENT_HUB.query,
            gold=CONVERGENT_HUB.gold,
            distractor=CONVERGENT_HUB.distractor,
            items=_convergent_hub_items(strip_convergent_edges=True),
        )
        store = self._load(stripped.items)
        try:
            self.assertNotIn(stripped.gold, _ids(store.search(stripped.query, k=1)))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
