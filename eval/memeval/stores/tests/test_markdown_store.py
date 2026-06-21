"""Unit tests for :class:`memeval.stores.markdown_store.MarkdownStore`.

The markdown backend is OKF-native (persistence delegates to ``OKFStore``) with an
inverted keyword index on top. These tests pin the behaviors that matter:

* it satisfies the frozen ``MemoryStore`` protocol;
* ``write``/``get``/``all`` round-trip, and persistence is a *conformant OKF bundle*
  on disk that a fresh store autoloads (durability);
* ``search`` returns ONLY genuine keyword matches (no zero-overlap padding),
  ranked by the same shared Okapi BM25 scorer the reference store uses, with
  ``rank``/``score``/``tokens`` set and ``as_of`` honored;
* overwriting an item updates both content and the index (no stale postings).

Stdlib-only; run from ``eval/``:  ``python3 -m unittest memeval.stores.tests.test_markdown_store``
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memeval.harness import InMemoryStore
from memeval.okf import validate_bundle
from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem
from memeval.stores.markdown_store import MarkdownStore


def _mk(item_id: str, content: str, *, timestamp: float = 0.0,
        relevancy: float = 1.0, tags=None) -> MemoryItem:
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy, tags=list(tags or []))


class MarkdownStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def store(self) -> MarkdownStore:
        return MarkdownStore(self.path)

    # -- contract -----------------------------------------------------------
    def test_satisfies_memorystore_protocol(self) -> None:
        self.assertIsInstance(self.store(), MemoryStore)

    # -- persistence --------------------------------------------------------
    def test_write_get_round_trip(self) -> None:
        s = self.store()
        s.write(_mk("m1", "deploy script uses kubernetes"))
        got = s.get("m1")
        self.assertIsNotNone(got)
        self.assertEqual(got.item_id, "m1")
        self.assertIn("kubernetes", got.content)
        self.assertIsNone(s.get("missing"))

    def test_persists_as_conformant_okf_and_autoloads(self) -> None:
        s = self.store()
        s.write(_mk("m1", "kubernetes deployment notes"))
        # On disk it is a conformant OKF bundle.
        self.assertEqual(validate_bundle(self.path), [])
        self.assertTrue(any(Path(self.path).rglob("*.md")))
        # Durability: a fresh store autoloads + re-indexes the bundle.
        s2 = MarkdownStore(self.path)
        self.assertIsNotNone(s2.get("m1"))
        hits = s2.search("kubernetes")
        self.assertTrue(hits and hits[0].item_id == "m1")

    # -- search semantics ---------------------------------------------------
    def test_search_sets_rank_score_tokens(self) -> None:
        s = self.store()
        s.write(_mk("m1", "kubernetes deployment guide"))
        hits = s.search("kubernetes")
        self.assertEqual(len(hits), 1)
        h = hits[0]
        self.assertEqual(h.rank, 0)
        self.assertGreater(h.score, 0.0)  # BM25 is non-negative & UNBOUNDED (not [0, 1])
        self.assertGreater(h.tokens, 0)  # efficiency metric needs a non-zero token count

    def test_returns_only_keyword_matches(self) -> None:
        s = self.store()
        s.write(_mk("a", "deploy script uses kubernetes"))
        s.write(_mk("b", "the cat sat on the mat"))
        s.write(_mk("c", "user prefers dark mode"))
        ids = [h.item_id for h in s.search("kubernetes deployment", k=5)]
        self.assertEqual(ids, ["a"])  # b and c never surface — no zero-overlap padding

    def test_empty_query_returns_empty(self) -> None:
        s = self.store()
        s.write(_mk("a", "anything at all"))
        self.assertEqual(s.search("   ", k=5), [])
        self.assertEqual(s.search("", k=5), [])

    def test_ranking_parity_with_reference_on_shared_candidates(self) -> None:
        rows = [
            ("a", "alpha shared token here", 0.5, 10.0),
            ("b", "shared token plus more shared", 0.9, 5.0),
            ("c", "token alone", 0.1, 1.0),
        ]
        ref, s = InMemoryStore(), self.store()
        for item_id, content, rel, ts in rows:
            ref.write(_mk(item_id, content, relevancy=rel, timestamp=ts))
            s.write(_mk(item_id, content, relevancy=rel, timestamp=ts))
        q = "shared token"
        ref_ids = [h.item_id for h in ref.search(q, k=10) if h.score > 0.0]
        mk_ids = [h.item_id for h in s.search(q, k=10)]
        self.assertEqual(mk_ids, ref_ids)  # identical ordering on genuinely-matching items

    def test_as_of_excludes_future_items(self) -> None:
        s = self.store()
        s.write(_mk("old", "kubernetes notes", timestamp=100.0))
        s.write(_mk("new", "kubernetes notes later", timestamp=200.0))
        ids = [h.item_id for h in s.search("kubernetes", k=5, as_of=150.0)]
        self.assertIn("old", ids)
        self.assertNotIn("new", ids)

    def test_overwrite_updates_content_and_index(self) -> None:
        s = self.store()
        s.write(_mk("m1", "kubernetes deployment"))
        s.write(_mk("m1", "completely different topic sailing"))  # same id, new content
        self.assertEqual(s.get("m1").content, "completely different topic sailing")
        self.assertEqual([h.item_id for h in s.search("kubernetes")], [])  # stale posting gone
        self.assertEqual([h.item_id for h in s.search("sailing")], ["m1"])


if __name__ == "__main__":
    unittest.main()
