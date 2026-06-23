"""Unit tests for :mod:`memeval.stores.rerankers`. Owner: Brent.

MOCK-ONLY — no live Voyage calls, no API key, no network. Coverage:

* :class:`MockReranker` — reorders candidates by query overlap; respects ``top_k``; records calls.
* :func:`rerank_items` — re-scores a list of ``RetrievedItem`` and resets ``rank`` 0..k-1.
* :class:`RerankedStore` — over-fetches ``rerank_top_n`` from an inner store then reranks to ``k``
  (anti-theater: a fixed bad inner order is provably reordered); delegates write/get/all; satisfies
  the ``MemoryStore`` protocol.
* :class:`VoyageReranker` guards — unset key raises ``RuntimeError``; the rerank request is shaped
  correctly and the response parsed/sorted (``urllib.request.urlopen`` monkeypatched — NO network);
  retry/backoff on 5xx; non-retryable 4xx; malformed-shape guard; no network at import/construction.

Offline note (D019/D020 lesson): an offline lexical reranker can only show the MECHANISM + reordering;
the retrieval-quality LIFT of a real cross-encoder reranker is a captained run, never in CI.

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_rerankers
"""

from __future__ import annotations

import json
import os
import socket
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from typing import Optional

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem, RetrievedItem
from memeval.stores.rerankers import (
    MockReranker,
    RerankedStore,
    VoyageReranker,
    rerank_items,
)


def _ri(item_id: str, content: str, score: float, rank: int) -> RetrievedItem:
    return RetrievedItem(item=MemoryItem(item_id=item_id, content=content), score=score, rank=rank)


# --------------------------------------------------------------------------- #
# Fakes: a store returning a FIXED candidate order (so reorder is provable), and
# the urlopen patch / canned response (mirrors test_embedders).
# --------------------------------------------------------------------------- #
class _FixedStore:
    """A MemoryStore whose ``search`` returns a fixed list and records the ``k`` it was asked for."""

    def __init__(self, items: list) -> None:
        self._items = items            # list[MemoryItem]
        self.search_k: Optional[int] = None

    def write(self, item: MemoryItem) -> None:
        self._items.append(item)

    def get(self, item_id: str):
        return next((m for m in self._items if m.item_id == item_id), None)

    def search(self, query: str, *, k: int = 5, as_of=None, **kwargs):
        self.search_k = k
        out = self._items[:k]
        return [RetrievedItem(item=m, score=1.0 - i * 0.01, rank=i) for i, m in enumerate(out)]

    def all(self):
        return list(self._items)

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [m for m in self._items if m.item_id != item_id]
        return len(self._items) < before


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._buf = BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _rerank_response(scores_by_index: list) -> bytes:
    """A Voyage-shaped rerank response: list of {index, relevance_score} (deliberately out of order)."""
    return json.dumps({
        "object": "list",
        "data": [{"index": i, "relevance_score": s} for i, s in scores_by_index],
        "model": "rerank-2.5",
        "usage": {"total_tokens": 9},
    }).encode("utf-8")


class _UrlopenPatch:
    def __init__(self, handler) -> None:
        self._handler = handler
        self._orig = None

    def __enter__(self):
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = self._handler
        return self

    def __exit__(self, *exc):
        urllib.request.urlopen = self._orig


class _VoyageEnvTestCase(unittest.TestCase):
    """Snapshot/remove VOYAGE_API_KEY so guard tests see a truly unset key regardless of run order."""

    def setUp(self) -> None:
        self._saved = os.environ.pop("VOYAGE_API_KEY", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["VOYAGE_API_KEY"] = self._saved
        else:
            os.environ.pop("VOYAGE_API_KEY", None)

    def set_key(self, value: str = "vk-test") -> None:
        os.environ["VOYAGE_API_KEY"] = value


# --------------------------------------------------------------------------- #
# MockReranker (offline, deterministic)
# --------------------------------------------------------------------------- #
class MockRerankerTests(unittest.TestCase):
    def test_reorders_by_query_overlap(self) -> None:
        mr = MockReranker()
        # Doc 1 overlaps the query far more than doc 0, but is listed second.
        ranked = mr("connection pool max size", [
            "the cat sat on the mat",                       # 0 — no overlap
            "the connection pool max size is twenty",       # 1 — high overlap
        ])
        self.assertEqual([i for i, _ in ranked], [1, 0], "highest-overlap doc must rank first")
        self.assertGreater(ranked[0][1], ranked[1][1], "scores descending")

    def test_respects_top_k(self) -> None:
        mr = MockReranker()
        ranked = mr("alpha beta", ["alpha", "beta", "gamma"], top_k=2)
        self.assertEqual(len(ranked), 2)

    def test_stable_tiebreak_by_index(self) -> None:
        mr = MockReranker()
        ranked = mr("zzz", ["foo", "bar"])  # zero overlap for both -> tie
        self.assertEqual([i for i, _ in ranked], [0, 1], "ties keep original index order")

    def test_records_calls(self) -> None:
        mr = MockReranker()
        mr("q", ["a", "b"], top_k=1)
        self.assertEqual(mr.calls, [("q", ["a", "b"], 1)])


# --------------------------------------------------------------------------- #
# rerank_items helper
# --------------------------------------------------------------------------- #
class RerankItemsTests(unittest.TestCase):
    def test_reorders_and_resets_rank(self) -> None:
        items = [
            _ri("a", "the cat sat on the mat", score=0.9, rank=0),       # inner-best, low overlap
            _ri("b", "connection pool max size is twenty", score=0.5, rank=1),
        ]
        out = rerank_items("connection pool max size", items, reranker=MockReranker(), k=2)
        self.assertEqual([r.item_id for r in out], ["b", "a"], "reranked: high-overlap first")
        self.assertEqual([r.rank for r in out], [0, 1], "ranks reset 0..k-1")
        self.assertGreaterEqual(out[0].score, out[1].score)

    def test_empty(self) -> None:
        self.assertEqual(rerank_items("q", [], reranker=MockReranker(), k=5), [])

    def test_non_positive_k_returns_empty(self) -> None:
        items = [_ri("a", "doc", score=1.0, rank=0)]
        self.assertEqual(rerank_items("q", items, reranker=MockReranker(), k=0), [])
        self.assertEqual(rerank_items("q", items, reranker=MockReranker(), k=-1), [])

    def test_rejects_out_of_range_index(self) -> None:
        items = [_ri("a", "doc", score=1.0, rank=0)]
        bad = lambda q, docs, *, top_k=None: [(5, 0.9)]  # index 5 for 1 candidate
        with self.assertRaises(ValueError):
            rerank_items("q", items, reranker=bad, k=1)

    def test_rejects_duplicate_index(self) -> None:
        items = [_ri("a", "d0", score=1.0, rank=0), _ri("b", "d1", score=0.9, rank=1)]
        dup = lambda q, docs, *, top_k=None: [(0, 0.9), (0, 0.8)]
        with self.assertRaises(ValueError):
            rerank_items("q", items, reranker=dup, k=2)

    def test_truncates_to_k(self) -> None:
        items = [_ri(f"m{i}", f"doc {i}", score=1.0 - i, rank=i) for i in range(5)]
        out = rerank_items("doc", items, reranker=MockReranker(), k=3)
        self.assertEqual(len(out), 3)


# --------------------------------------------------------------------------- #
# RerankedStore (MemoryStore facade)
# --------------------------------------------------------------------------- #
class RerankedStoreTests(unittest.TestCase):
    def _store(self, rerank_top_n=50):
        inner = _FixedStore([
            MemoryItem(item_id="a", content="the cat sat on the mat"),       # low overlap, returned first
            MemoryItem(item_id="b", content="connection pool max size is twenty"),  # high overlap
        ])
        return RerankedStore(inner, MockReranker(), rerank_top_n=rerank_top_n), inner

    def test_satisfies_memorystore_protocol(self) -> None:
        rs, _ = self._store()
        self.assertIsInstance(rs, MemoryStore)

    def test_reranks_the_inner_order(self) -> None:
        # Anti-theater: the inner store returns [a, b]; the reranker must surface b first.
        rs, _ = self._store()
        hits = rs.search("connection pool max size", k=2)
        self.assertEqual([h.item_id for h in hits], ["b", "a"], "RerankedStore must apply the reranker")
        self.assertEqual([h.rank for h in hits], [0, 1])

    def test_over_fetches_rerank_top_n_then_returns_k(self) -> None:
        rs, inner = self._store(rerank_top_n=25)
        hits = rs.search("anything", k=2)
        self.assertEqual(inner.search_k, 25, "must over-fetch max(k, rerank_top_n) from the inner store")
        self.assertLessEqual(len(hits), 2, "returns only k after reranking")

    def test_k_larger_than_top_n_fetches_k(self) -> None:
        rs, inner = self._store(rerank_top_n=5)
        rs.search("anything", k=10)
        self.assertEqual(inner.search_k, 10, "fetch = max(k, rerank_top_n)")

    def test_non_positive_k_does_no_work(self) -> None:
        # k<=0 must return [] WITHOUT fetching from the inner store or calling the (paid) reranker.
        inner = _FixedStore([MemoryItem(item_id="a", content="x")])
        mr = MockReranker()
        rs = RerankedStore(inner, mr, rerank_top_n=50)
        self.assertEqual(rs.search("q", k=0), [])
        self.assertEqual(rs.search("q", k=-3), [])
        self.assertIsNone(inner.search_k, "inner store must not be queried for k<=0")
        self.assertEqual(mr.calls, [], "reranker must not be called for k<=0")

    def test_delegates_write_get_all(self) -> None:
        rs, inner = self._store()
        rs.write(MemoryItem(item_id="c", content="new memory"))
        self.assertIsNotNone(rs.get("c"))
        self.assertIn("c", {m.item_id for m in rs.all()})
        self.assertIsNone(rs.get("missing"))


# --------------------------------------------------------------------------- #
# VoyageReranker guards — NO network (urlopen monkeypatched / never called)
# --------------------------------------------------------------------------- #
class VoyageRerankerGuardTests(_VoyageEnvTestCase):
    def test_missing_key_raises(self) -> None:
        rr = VoyageReranker()
        with self.assertRaises(RuntimeError) as ctx:
            rr("q", ["a", "b"])
        self.assertIn("VOYAGE_API_KEY", str(ctx.exception))

    def test_payload_shape_and_parse_sorted(self) -> None:
        self.set_key("vk-test-DO-NOT-LOG")
        captured: dict = {}

        def handler(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            # Return scores out of natural order: doc 1 is most relevant.
            return _FakeResponse(_rerank_response([(0, 0.10), (1, 0.90)]))

        rr = VoyageReranker()
        with _UrlopenPatch(handler):
            ranked = rr("the query", ["doc zero", "doc one"])

        self.assertEqual(ranked, [(1, 0.90), (0, 0.10)], "sorted by relevance desc as (index, score)")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/v1/rerank"))
        self.assertEqual(captured["auth"], "Bearer vk-test-DO-NOT-LOG")
        body = captured["body"]
        self.assertEqual(body["model"], "rerank-2.5")
        self.assertEqual(body["query"], "the query")
        self.assertEqual(body["documents"], ["doc zero", "doc one"])

    def test_top_k_sent_when_given(self) -> None:
        self.set_key()
        captured: dict = {}

        def handler(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(_rerank_response([(0, 0.5)]))

        rr = VoyageReranker()
        with _UrlopenPatch(handler):
            rr("q", ["only doc"], top_k=1)
        self.assertEqual(captured["body"]["top_k"], 1)

    def test_empty_documents_makes_no_request(self) -> None:
        def handler(request, timeout=None):  # pragma: no cover - must not be called
            raise AssertionError("urlopen must not be called for empty documents")

        rr = VoyageReranker()
        with _UrlopenPatch(handler):
            self.assertEqual(rr("q", []), [])

    def test_retry_on_5xx_then_succeeds(self) -> None:
        self.set_key()
        attempts = {"n": 0}
        slept: list = []

        def handler(request, timeout=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise urllib.error.HTTPError(request.full_url, 503, "x", hdrs=None, fp=BytesIO(b"{}"))
            return _FakeResponse(_rerank_response([(0, 0.5)]))

        rr = VoyageReranker(max_retries=3, sleeper=lambda s: slept.append(s))
        with _UrlopenPatch(handler):
            ranked = rr("q", ["a"])
        self.assertEqual(ranked, [(0, 0.5)])
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(slept), 2)

    def test_non_retryable_4xx_raises(self) -> None:
        self.set_key()
        attempts = {"n": 0}

        def handler(request, timeout=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(request.full_url, 400, "Bad", hdrs=None, fp=BytesIO(b"{}"))

        rr = VoyageReranker(sleeper=lambda s: None)
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                rr("q", ["a"])
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(attempts["n"], 1)

    def test_timeout_is_retried_then_wrapped(self) -> None:
        self.set_key()
        attempts = {"n": 0}

        def handler(request, timeout=None):
            attempts["n"] += 1
            raise socket.timeout("timed out")

        rr = VoyageReranker(max_retries=2, sleeper=lambda s: None)
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                rr("q", ["a"])
        self.assertEqual(attempts["n"], 3)
        self.assertIsInstance(ctx.exception.__cause__, TimeoutError)
        self.assertNotIn("timed out", str(ctx.exception))

    def test_malformed_shape_raises(self) -> None:
        self.set_key()

        def handler(request, timeout=None):
            return _FakeResponse(json.dumps(["not", "a", "dict"]).encode("utf-8"))

        rr = VoyageReranker()
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                rr("q", ["a"])
        self.assertIn("shape", str(ctx.exception).lower())

    def test_missing_relevance_score_raises(self) -> None:
        self.set_key()

        def handler(request, timeout=None):
            body = json.dumps({"data": [{"index": 0}]}).encode("utf-8")  # no relevance_score
            return _FakeResponse(body)

        rr = VoyageReranker()
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                rr("q", ["a"])
        self.assertIn("shape", str(ctx.exception).lower())

    def test_out_of_range_index_raises(self) -> None:
        self.set_key()

        def handler(request, timeout=None):
            # index 3 for a single document -> malformed; must surface as the shape RuntimeError.
            return _FakeResponse(_rerank_response([(3, 0.9)]))

        rr = VoyageReranker()
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                rr("q", ["only one doc"])
        self.assertIn("shape", str(ctx.exception).lower())

    def test_no_network_at_construction(self) -> None:
        def handler(request, timeout=None):  # pragma: no cover - must not be called
            raise AssertionError("construction must not call the network")

        with _UrlopenPatch(handler):
            rr = VoyageReranker()
        self.assertEqual(rr.model, "rerank-2.5")


# --------------------------------------------------------------------------- #
# VoyageReranker through RerankedStore (still no network)
# --------------------------------------------------------------------------- #
class VoyageThroughRerankedStoreTests(_VoyageEnvTestCase):
    def test_reranked_store_uses_voyage_scores(self) -> None:
        self.set_key()

        def handler(request, timeout=None):
            # Whatever the candidates, declare index 1 the most relevant.
            body = json.loads(request.data.decode("utf-8"))
            n = len(body["documents"])
            scores = [(i, 0.99 if i == 1 else 0.10) for i in range(n)]
            return _FakeResponse(_rerank_response(scores))

        inner = _FixedStore([
            MemoryItem(item_id="a", content="first"),
            MemoryItem(item_id="b", content="second"),
        ])
        rs = RerankedStore(inner, VoyageReranker(), rerank_top_n=10)
        with _UrlopenPatch(handler):
            hits = rs.search("q", k=2)
        self.assertEqual([h.item_id for h in hits], ["b", "a"], "Voyage relevance order wins")
        self.assertAlmostEqual(hits[0].score, 0.99)


if __name__ == "__main__":
    unittest.main()
