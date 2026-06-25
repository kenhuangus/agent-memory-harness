"""Parity tests for FTS5 shared ranking against MarkdownStore."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from memeval.schema import MemoryItem
from memeval.stores.fts5_store import Fts5Store
from memeval.stores.markdown_store import MarkdownStore


def _fts5_available() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(content, tokenize='unicode61')")
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _clone(item: MemoryItem) -> MemoryItem:
    return MemoryItem(
        item_id=item.item_id,
        content=item.content,
        timestamp=item.timestamp,
        relevancy=item.relevancy,
        session_id=item.session_id,
        source=item.source,
        tags=list(item.tags),
        tokens=item.tokens,
        version=item.version,
        metadata=dict(item.metadata or {}),
    )


@unittest.skipUnless(_fts5_available(), "SQLite FTS5 unavailable")
class Fts5SharedParityTests(unittest.TestCase):
    def test_shared_ranking_matches_markdown_ordering(self) -> None:
        items = [
            MemoryItem("a", "alpha beta recipe", timestamp=10.0, relevancy=0.5),
            MemoryItem("b", "alpha beta beta recipe", timestamp=20.0, relevancy=0.4),
            MemoryItem("c", "gamma delta archive", timestamp=30.0, relevancy=0.9),
            MemoryItem("d", "alpha caravan recipe", timestamp=40.0, relevancy=0.8),
            MemoryItem("e", "plain notes only", timestamp=50.0, relevancy=1.0),
        ]
        queries = [
            "alpha beta",
            "recipe",
            "gamma delta",
            "missing token",
            "alpha",
        ]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            markdown = MarkdownStore(root / "markdown")
            fts5 = Fts5Store(str(root / "fts5.db"), ranking="shared")
            try:
                for item in items:
                    markdown.write(_clone(item))
                    fts5.write(_clone(item))
                for query in queries:
                    with self.subTest(query=query):
                        markdown_ids = [h.item_id for h in markdown.search(query, k=10)]
                        fts5_ids = [h.item_id for h in fts5.search(query, k=10)]
                        self.assertEqual(fts5_ids, markdown_ids)
            finally:
                fts5.close()


if __name__ == "__main__":
    unittest.main()
