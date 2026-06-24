"""Tests for rerank-in-the-router (Track A): the reranker is a ``RouterConfig`` field that ``route()``
applies — the same read-orchestration layer as fusion, NOT a caller-side wrapper. Verifies a profile
without a reranker is byte-for-byte today (no rerank), a profile WITH one wraps the routed retriever in a
``RerankedStore``, the reranker is genuinely consulted on search, and ordering follows the (offline)
lexical reranker. The real semantic rerank LIFT is the captained Voyage run (D045), not asserted here."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from memeval.router import (
    GRAPH,
    MARKDOWN,
    VECTORS,
    Router,
    RouterStore,
    fusion_profile,
    speed_profile,
)
from memeval.schema import MemoryItem
from memeval.stores import GraphStore, MarkdownStore, SqliteVectorStore
from memeval.stores.rerankers import MockReranker, RerankedStore


def _backends(tmp: Path, contents: list[str]) -> dict:
    backends = {
        MARKDOWN: MarkdownStore(tmp / "md"),
        VECTORS: SqliteVectorStore(str(tmp / "v.db")),
        GRAPH: GraphStore(path=str(tmp / "g.db")),
    }
    for i, content in enumerate(contents):
        item = MemoryItem(item_id=f"m{i}", content=content)
        for store in backends.values():
            store.write(item)
    return backends


def _fusion_rerank(backends: dict, reranker, *, rerank_top_n: int = 10):
    return RouterStore(Router.with_config(
        backends, fusion_profile(method="rrf", per_backend_k=10,
                                 reranker=reranker, rerank_top_n=rerank_top_n)))


class RouterRerankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rrk_"))
        self.backends = _backends(self.tmp, [
            "alpha widget configuration guide",
            "beta gadget calibration steps",
            "gamma sprocket calibration and tuning notes",
        ])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_reranker_route_is_not_reranked(self) -> None:
        # A profile without a reranker -> route() returns the base retriever, no RerankedStore wrap.
        retriever = Router.with_config(self.backends, fusion_profile()).route("calibration")
        self.assertNotIsInstance(retriever, RerankedStore)

    def test_reranker_wraps_the_routed_retriever(self) -> None:
        retriever = Router.with_config(
            self.backends, fusion_profile(reranker=MockReranker())).route("calibration")
        self.assertIsInstance(retriever, RerankedStore)

    def test_profiles_carry_the_reranker(self) -> None:
        rr = MockReranker()
        cfg = fusion_profile(reranker=rr, rerank_top_n=25)
        self.assertIs(cfg.reranker, rr)
        self.assertEqual(cfg.rerank_top_n, 25)
        self.assertIsNone(speed_profile().reranker)  # speed is byte-for-byte today (no rerank)

    def test_reranker_is_consulted_on_search(self) -> None:
        reranker = MockReranker()
        hits = _fusion_rerank(self.backends, reranker).search("calibration", k=3)
        self.assertTrue(reranker.calls, "reranker was not consulted on search")
        self.assertLessEqual(len(hits), 3)
        self.assertEqual([h.rank for h in hits], list(range(len(hits))))  # rerank resets ranks 0..k-1

    def test_orders_by_lexical_reranker(self) -> None:
        # MockReranker scores by token-overlap (Jaccard); "gamma sprocket calibration and tuning notes"
        # (m2) has the strongest overlap, so it must be the reranked top-1.
        top = [h.item_id for h in _fusion_rerank(self.backends, MockReranker()).search(
            "sprocket calibration tuning", k=3)]
        self.assertEqual(top[0], "m2")


if __name__ == "__main__":
    unittest.main()
