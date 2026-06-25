"""Unit tests for :class:`memeval.stores.fts5_store.Fts5Store`."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem
from memeval.stores.fts5_store import Fts5Store


def _fts5_available() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(content, tokenize='unicode61')")
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _mk(
    item_id: str,
    content: str,
    *,
    timestamp: float = 0.0,
    relevancy: float = 1.0,
    tags=None,
) -> MemoryItem:
    return MemoryItem(
        item_id=item_id,
        content=content,
        timestamp=timestamp,
        relevancy=relevancy,
        tags=list(tags or []),
    )


@unittest.skipUnless(_fts5_available(), "SQLite FTS5 unavailable")
class Fts5StoreTests(unittest.TestCase):
    def store(self, path: str = ":memory:", *, ranking: str = "native") -> Fts5Store:
        return Fts5Store(path, ranking=ranking)

    def test_satisfies_memorystore_protocol(self) -> None:
        s = self.store()
        try:
            self.assertIsInstance(s, MemoryStore)
        finally:
            s.close()

    def test_write_get_round_trip(self) -> None:
        s = self.store()
        try:
            s.write(_mk("m1", "configuration settings for the app", tags=["cfg"]))
            got = s.get("m1")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.item_id, "m1")
            self.assertIn("configuration", got.content)
            self.assertEqual(got.tags, ["cfg"])
            self.assertGreater(got.tokens, 0)
            self.assertIsNone(s.get("missing"))
        finally:
            s.close()

    def test_all_returns_written_items(self) -> None:
        s = self.store()
        try:
            s.write(_mk("a", "alpha"))
            s.write(_mk("b", "beta"))
            self.assertEqual(sorted(i.item_id for i in s.all()), ["a", "b"])
        finally:
            s.close()

    def test_overwrite_is_idempotent_and_refreshes_index(self) -> None:
        s = self.store()
        try:
            s.write(_mk("m1", "first content"))
            s.write(_mk("m1", "second content"))
            got = s.get("m1")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.content, "second content")
            self.assertEqual(len(s.all()), 1)
            self.assertEqual([h.item_id for h in s.search("second", k=5)], ["m1"])
            self.assertEqual([h.item_id for h in s.search("first", k=5)], [])
        finally:
            s.close()

    def test_search_sets_descending_score_rank_and_tokens(self) -> None:
        s = self.store()
        try:
            s.write(_mk("a", "alpha beta beta"))
            s.write(_mk("b", "alpha gamma"))
            s.write(_mk("c", "unrelated delta"))
            hits = s.search("alpha beta", k=5)
            self.assertGreaterEqual(len(hits), 2)
            self.assertEqual([h.rank for h in hits], list(range(len(hits))))
            self.assertTrue(all(h.tokens > 0 for h in hits))
            scores = [h.score for h in hits]
            self.assertEqual(scores, sorted(scores, reverse=True))
        finally:
            s.close()

    def test_empty_query_returns_empty(self) -> None:
        s = self.store()
        try:
            s.write(_mk("a", "configuration"))
            self.assertEqual(s.search("", k=5), [])
            self.assertEqual(s.search("   ", k=5), [])
        finally:
            s.close()

    def test_special_character_queries_do_not_raise(self) -> None:
        s = self.store()
        try:
            s.write(_mk("a", "foo a b x"))
            for query in ('foo"', "a AND b", "x:"):
                with self.subTest(query=query):
                    hits = s.search(query, k=5)
                    self.assertIsInstance(hits, list)
        finally:
            s.close()

    def test_as_of_is_inclusive_at_equality(self) -> None:
        s = self.store()
        try:
            s.write(_mk("old", "configuration", timestamp=100.0))
            s.write(_mk("at", "configuration", timestamp=150.0))
            s.write(_mk("future", "configuration", timestamp=200.0))
            ids = [h.item_id for h in s.search("configuration", k=5, as_of=150.0)]
            self.assertIn("old", ids)
            self.assertIn("at", ids)
            self.assertNotIn("future", ids)
        finally:
            s.close()

    def test_delete_is_idempotent_and_updates_index(self) -> None:
        s = self.store()
        try:
            s.write(_mk("m1", "configuration"))
            self.assertTrue(s.delete("m1"))
            self.assertFalse(s.delete("m1"))
            self.assertFalse(s.delete("missing"))
            self.assertIsNone(s.get("m1"))
            self.assertEqual(s.search("configuration", k=5), [])
        finally:
            s.close()

    def test_file_backed_store_uses_wal(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "mem.db")
            s = self.store(path)
            try:
                mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(mode.lower(), "wal")
            finally:
                s.close()

    def test_close_is_idempotent(self) -> None:
        s = self.store()
        s.close()
        s.close()

    def test_native_scores_are_non_negative_and_non_increasing(self) -> None:
        s = self.store()
        try:
            s.write(_mk("a", "alpha alpha beta"))
            s.write(_mk("b", "alpha beta"))
            hits = s.search("alpha beta", k=5)
            self.assertTrue(hits)
            scores = [h.score for h in hits]
            self.assertTrue(all(score >= 0.0 for score in scores))
            self.assertEqual(scores, sorted(scores, reverse=True))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
