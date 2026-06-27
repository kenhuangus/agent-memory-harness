"""Tests for the recall score FLOOR (``RouterConfig.recall_min_score``) — owner: Brent.

Recall is pure top-k with NO floor, so it returns the full ``k`` even when every match is weak. The
floor is the precision counterpart: drop FINAL hits scoring below a threshold, allowing a result with
fewer than ``k`` items (or none). It is applied by :meth:`Router.route` as the OUTERMOST wrapper — AFTER
any rerank / fusion / cascade — so it filters the final, user-facing scores. ``recall_min_score=None``
(the offline/eval default) is byte-for-byte today: no wrap, no filtering.

Run from ``eval/``:  ``python3 -m unittest memeval.stores.tests.test_router_score_floor``
"""

from __future__ import annotations

import unittest
from typing import Any, Optional

from memeval.router import (
    GRAPH,
    MARKDOWN,
    VECTORS,
    Router,
    RouterConfig,
    RouterStore,
    _ScoreFloorStore,
)
from memeval.schema import MemoryItem, RetrievedItem
from memeval.stores.rerankers import MockReranker


class _ScoredStore:
    """A deterministic :class:`~memeval.protocols.MemoryStore` returning pre-assigned ``(id, score)``
    hits, ignoring the query — so a test can pin exact FINAL scores and the floor's keep/drop boundary is
    unambiguous (no BM25 / cosine in the loop). ``rank`` is the 0-based position; ``get`` / ``all`` /
    ``write`` / ``delete`` satisfy the five-method protocol minimally."""

    def __init__(self, scored: list[tuple[str, float]], *, content: Optional[dict] = None) -> None:
        self._scored = list(scored)
        self._items = {
            iid: MemoryItem(item_id=iid, content=(content or {}).get(iid, f"content for {iid}"))
            for iid, _ in scored
        }

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list[RetrievedItem]:
        return [RetrievedItem(item=self._items[iid], score=score, rank=rank)
                for rank, (iid, score) in enumerate(self._scored[:k])]

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._items.get(item_id)

    def all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def write(self, item: MemoryItem) -> None:
        self._items[item.item_id] = item

    def delete(self, item_id: str) -> bool:
        return self._items.pop(item_id, None) is not None


def _routed(store: _ScoredStore, min_score: Optional[float]) -> RouterStore:
    """A RouterStore over ``store`` registered under every backend name, so any ``classify()`` choice
    resolves to it — isolating the floor from routing."""
    cfg = RouterConfig(recall_min_score=min_score)
    backends = {GRAPH: store, VECTORS: store, MARKDOWN: store}
    return RouterStore(Router.with_config(backends, cfg))


# The calibration story in miniature: two garbage hits (<= 0.09), a gap, two real matches (>= 0.189).
SCORED = [("a", 0.40), ("b", 0.189), ("c", 0.09), ("d", 0.03)]


class ScoreFloorTests(unittest.TestCase):
    def test_none_is_byte_identical_no_filtering(self) -> None:
        # recall_min_score=None -> route() adds no wrapper; the recall is exactly today's full top-k.
        hits = _routed(_ScoredStore(SCORED), None).search("anything", k=5)
        self.assertEqual([h.item_id for h in hits], ["a", "b", "c", "d"])
        self.assertEqual([h.score for h in hits], [0.40, 0.189, 0.09, 0.03])

    def test_floor_drops_below_keeps_at_or_above(self) -> None:
        hits = _routed(_ScoredStore(SCORED), 0.15).search("anything", k=5)
        self.assertEqual([h.item_id for h in hits], ["a", "b"])  # c=0.09, d=0.03 dropped
        # Surviving hits' fields + order are otherwise unchanged (original scores + ranks, no re-rank).
        self.assertEqual([h.score for h in hits], [0.40, 0.189])
        self.assertEqual([h.rank for h in hits], [0, 1])

    def test_all_below_floor_returns_empty_no_backfill(self) -> None:
        # Every candidate is garbage -> [] is the correct precision outcome (NOT backfilled to k).
        hits = _routed(_ScoredStore([("c", 0.09), ("d", 0.03)]), 0.15).search("anything", k=5)
        self.assertEqual(hits, [])

    def test_boundary_is_inclusive(self) -> None:
        # score == floor is KEPT (drop is strictly `< min_score`).
        hits = _routed(_ScoredStore([("x", 0.15)]), 0.15).search("q", k=5)
        self.assertEqual([h.item_id for h in hits], ["x"])

    def test_non_positive_floor_disables(self) -> None:
        for floor in (0.0, -1.0):
            with self.subTest(floor=floor):
                hits = _routed(_ScoredStore(SCORED), floor).search("q", k=5)
                self.assertEqual([h.item_id for h in hits], ["a", "b", "c", "d"])

    def test_route_wraps_only_when_floor_positive(self) -> None:
        store = _ScoredStore(SCORED)
        backends = {GRAPH: store, VECTORS: store, MARKDOWN: store}
        # None / <= 0 -> the raw backend is returned (byte-identical, no wrap).
        self.assertNotIsInstance(
            Router.with_config(backends, RouterConfig()).route("q"), _ScoreFloorStore)
        self.assertNotIsInstance(
            Router.with_config(backends, RouterConfig(recall_min_score=0.0)).route("q"), _ScoreFloorStore)
        # positive -> wrapped in the floor view.
        self.assertIsInstance(
            Router.with_config(backends, RouterConfig(recall_min_score=0.15)).route("q"), _ScoreFloorStore)

    def test_floor_filters_post_rerank_scores(self) -> None:
        # The floor must see the FINAL (post-rerank) scores, not the inner retriever's raw scores. The
        # inner store emits uniformly HIGH raw scores (0.9) that would all survive a 0.6 floor; the
        # MockReranker re-scores by token-overlap (Jaccard) with the query, and the floor filters THOSE.
        #   query "calibration tuning":
        #     m0 "calibration tuning notes" -> Jaccard 2/3 = 0.667  (kept,  >= 0.6)
        #     m1 "calibration"              -> Jaccard 1/2 = 0.5     (dropped, < 0.6)
        #     m2 "totally unrelated text"   -> Jaccard 0             (dropped, < 0.6)
        store = _ScoredStore(
            [("m0", 0.9), ("m1", 0.9), ("m2", 0.9)],
            content={"m0": "calibration tuning notes", "m1": "calibration",
                     "m2": "totally unrelated text"},
        )
        cfg = RouterConfig(reranker=MockReranker(), rerank_top_n=10, recall_min_score=0.6)
        rs = RouterStore(Router.with_config({GRAPH: store, VECTORS: store, MARKDOWN: store}, cfg))
        hits = rs.search("calibration tuning", k=3)
        self.assertEqual([h.item_id for h in hits], ["m0"])  # only the post-rerank >= 0.6 survives
        self.assertGreaterEqual(hits[0].score, 0.6)
        # Sanity: without the floor, the same reranked recall keeps all three (proves the floor did it).
        cfg_no_floor = RouterConfig(reranker=MockReranker(), rerank_top_n=10)
        rs2 = RouterStore(Router.with_config({GRAPH: store, VECTORS: store, MARKDOWN: store}, cfg_no_floor))
        self.assertEqual(len(rs2.search("calibration tuning", k=3)), 3)


if __name__ == "__main__":
    unittest.main()
