"""Unit tests for :class:`memeval.stores.sqlite_store.SqliteVectorStore`. Owner: Brent.

v1 is stdlib-only: `sqlite3` storage + a deterministic char-n-gram feature-hashing
embedder + brute-force cosine. Real dense embeddings (Voyage/bge) + ANN (HNSW/FAISS)
are injected/lazy on the paid path and NOT exercised here. So these tests verify the
**plumbing** and the **fuzzy-match ordering** the offline embedder buys us — NOT true
dense semantic quality (that's a paid-path eval, deferred).

Run from `eval/`:  python3 -m unittest memeval.stores.tests.test_sqlite_store
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem
from memeval.stores.sqlite_store import SqliteVectorStore


def _mk(item_id: str, content: str, *, timestamp: float = 0.0,
        relevancy: float = 1.0, tags=None) -> MemoryItem:
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy, tags=list(tags or []))


class SqliteVectorStoreTests(unittest.TestCase):
    def store(self, path: str = ":memory:") -> SqliteVectorStore:
        return SqliteVectorStore(path)

    def test_satisfies_memorystore_protocol(self) -> None:
        self.assertIsInstance(self.store(), MemoryStore)

    def test_write_get_round_trip(self) -> None:
        s = self.store()
        s.write(_mk("m1", "configuration settings for the app", tags=["cfg"]))
        got = s.get("m1")
        self.assertIsNotNone(got)
        self.assertEqual(got.item_id, "m1")
        self.assertIn("configuration", got.content)
        self.assertEqual(got.tags, ["cfg"])
        self.assertGreater(got.tokens, 0)  # populated on write (efficiency metric needs it)
        self.assertIsNone(s.get("missing"))

    def test_all_returns_written_items(self) -> None:
        s = self.store()
        s.write(_mk("a", "alpha"))
        s.write(_mk("b", "beta"))
        self.assertEqual(sorted(i.item_id for i in s.all()), ["a", "b"])

    def test_search_sets_rank_score_tokens(self) -> None:
        s = self.store()
        s.write(_mk("m1", "configuration settings"))
        hits = s.search("configuration", k=5)
        self.assertTrue(hits)
        h = hits[0]
        self.assertEqual(h.rank, 0)
        self.assertLessEqual(h.score, 1.0 + 1e-9)
        self.assertGreater(h.tokens, 0)

    def test_search_ranks_more_similar_first(self) -> None:
        s = self.store()
        s.write(_mk("cfg", "configuration settings for the service"))
        s.write(_mk("cat", "the cat sat on the mat"))
        ids = [h.item_id for h in s.search("config options", k=5)]
        self.assertEqual(ids[0], "cfg")

    def test_fuzzy_match_beats_unrelated(self) -> None:
        # char-n-grams catch morphology the keyword store can't: 'config' ~ 'configuration'
        s = self.store()
        s.write(_mk("cfg", "configuration"))
        s.write(_mk("unrelated", "banana smoothie recipe"))
        hits = s.search("config", k=2)
        self.assertEqual(hits[0].item_id, "cfg")
        self.assertGreater(hits[0].score, hits[1].score)

    def test_as_of_excludes_future_items(self) -> None:
        s = self.store()
        s.write(_mk("old", "configuration", timestamp=100.0))
        s.write(_mk("new", "configuration", timestamp=200.0))
        ids = [h.item_id for h in s.search("configuration", k=5, as_of=150.0)]
        self.assertIn("old", ids)
        self.assertNotIn("new", ids)

    def test_overwrite_is_idempotent_on_id(self) -> None:
        s = self.store()
        s.write(_mk("m1", "first content"))
        s.write(_mk("m1", "second content"))
        self.assertEqual(s.get("m1").content, "second content")
        self.assertEqual(len(s.all()), 1)

    def test_persists_to_disk_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "mem.db")
            s = SqliteVectorStore(path)
            s.write(_mk("m1", "configuration settings"))
            s.close()
            s2 = SqliteVectorStore(path)  # a fresh instance reads the file
            self.assertIsNotNone(s2.get("m1"))
            self.assertTrue(s2.search("configuration", k=1))
            s2.close()

    def test_uses_injected_embedder(self) -> None:
        # the headline deferral claim: a real embedder is injected via embed=
        def embed(text: str):
            return [1.0, 0.0] if "alpha" in (text or "").lower() else [0.0, 1.0]
        s = SqliteVectorStore(":memory:", embed=embed)
        s.write(_mk("a", "alpha one"))
        s.write(_mk("b", "beta two"))
        self.assertEqual(s.search("alpha query", k=2)[0].item_id, "a")

    def test_full_field_round_trip(self) -> None:
        s = self.store()
        s.write(MemoryItem(item_id="m1", content="x configuration", timestamp=5.0,
                           relevancy=0.7, session_id="s1", source="agent",
                           tags=["t1", "t2"], version=3, metadata={"k": "v", "n": 2}))
        got = s.get("m1")
        self.assertEqual(got.session_id, "s1")
        self.assertEqual(got.source, "agent")
        self.assertEqual(got.version, 3)
        self.assertAlmostEqual(got.relevancy, 0.7)
        self.assertEqual(got.tags, ["t1", "t2"])
        self.assertEqual(got.metadata, {"k": "v", "n": 2})

    def test_as_of_is_inclusive_at_equality(self) -> None:
        s = self.store()
        s.write(_mk("at", "configuration", timestamp=150.0))
        self.assertIn("at", [h.item_id for h in s.search("configuration", k=5, as_of=150.0)])

    def test_empty_query_returns_empty(self) -> None:
        s = self.store()
        s.write(_mk("a", "configuration"))
        self.assertEqual(s.search("", k=5), [])
        self.assertEqual(s.search("   ", k=5), [])


if __name__ == "__main__":
    unittest.main()
