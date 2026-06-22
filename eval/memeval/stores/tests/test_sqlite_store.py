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
from memeval.stores.sqlite_store import (
    SqliteVectorStore,
    _HashingEmbedder,
    _embedder_accepts_input_type,
)


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

    def test_file_backed_store_uses_wal(self) -> None:
        # ADR-P2: WAL is mandatory so concurrent cross-process writers/readers over one
        # $MEMORY_STORE file don't block. A file-backed store must report journal_mode == 'wal'.
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "mem.db")
            s = SqliteVectorStore(path)
            mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
            s.close()
            self.assertEqual(mode.lower(), "wal", "file-backed store must use WAL journal mode")

    def test_in_memory_store_is_unaffected_by_wal_pragma(self) -> None:
        # The WAL pragma is a no-op for :memory: (returns 'memory', never errors) — the default
        # offline path keeps working, write/search round-trip intact.
        s = SqliteVectorStore()  # :memory:
        mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "memory")
        s.write(_mk("m1", "offline path still works"))
        self.assertTrue(s.search("offline", k=1))
        s.close()

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


class EmbedSeamInputTypeTests(unittest.TestCase):
    """The store-internal query/document ``input_type`` seam (backward-compatible).

    The store passes ``input_type`` ('document' on write, 'query' on search) only to
    embedders whose signature accepts it; the offline hashing default accepts-and-
    ignores it, and a legacy one-arg ``text -> vector`` embedder is called positionally
    exactly as before. None of this is part of the frozen ``MemoryStore`` contract.
    """

    def test_accepts_input_type_detection(self) -> None:
        # hashing default + kwarg-aware callables -> True
        self.assertTrue(_embedder_accepts_input_type(_HashingEmbedder()))

        def kw(text, *, input_type=None):
            return [0.0]
        self.assertTrue(_embedder_accepts_input_type(kw))

        def varkw(text, **kwargs):
            return [0.0]
        self.assertTrue(_embedder_accepts_input_type(varkw))

        # legacy one-arg callable -> False (called positionally)
        def one_arg(text):
            return [0.0]
        self.assertFalse(_embedder_accepts_input_type(one_arg))

    def test_hashing_embedder_accepts_and_ignores_input_type(self) -> None:
        emb = _HashingEmbedder()
        base = emb("configuration settings")
        self.assertEqual(emb("configuration settings", input_type="document"), base)
        self.assertEqual(emb("configuration settings", input_type="query"), base)

    def test_kwarg_aware_embedder_receives_document_and_query(self) -> None:
        seen: list = []

        def recording(text, *, input_type=None):
            seen.append(input_type)
            return [1.0, 0.0] if "alpha" in (text or "").lower() else [0.0, 1.0]

        s = SqliteVectorStore(":memory:", embed=recording)
        s.write(_mk("a", "alpha one"))
        s.search("alpha query", k=1)
        self.assertEqual(seen, ["document", "query"])

    def test_legacy_one_arg_embedder_not_passed_input_type(self) -> None:
        # A strict one-arg embedder that rejects any kwarg must still work: the store
        # must call it positionally, never with input_type=.
        def strict_one_arg(text):
            return [1.0, 0.0] if "alpha" in (text or "").lower() else [0.0, 1.0]

        s = SqliteVectorStore(":memory:", embed=strict_one_arg)
        s.write(_mk("a", "alpha one"))   # would TypeError if input_type were passed
        self.assertEqual(s.search("alpha", k=1)[0].item_id, "a")

    def test_sole_positional_named_input_type_is_called_positionally(self) -> None:
        # An embedder whose SOLE positional param is (coincidentally) named
        # ``input_type`` is a legacy one-arg ``text -> vector`` callable, NOT the
        # query/document seam: classifying it as accepting would make the store call
        # embed(text, input_type=text) and collide on that single parameter. It must be
        # detected False and called positionally with the text.
        seen: list = []

        def embed(input_type):  # sole positional; the name is a red herring
            seen.append(input_type)
            return [1.0, 0.0] if "alpha" in (input_type or "").lower() else [0.0, 1.0]

        self.assertFalse(_embedder_accepts_input_type(embed))
        s = SqliteVectorStore(":memory:", embed=embed)
        s.write(_mk("a", "alpha one"))   # must NOT raise TypeError on write
        self.assertEqual(s.search("alpha", k=1)[0].item_id, "a")
        # the param received the TEXT positionally, never input_type='document'/'query'
        self.assertEqual(seen, ["alpha one", "alpha"])

    def test_second_positional_or_keyword_input_type_still_accepted(self) -> None:
        # Conversely, ``def embed(text, input_type=None)`` (input_type is the SECOND
        # positional-or-keyword param) stays accepted: embed(text, input_type=...) is
        # unambiguous there, so this currently-correct shape must remain True.
        def embed(text, input_type=None):
            return [0.0]
        self.assertTrue(_embedder_accepts_input_type(embed))


if __name__ == "__main__":
    unittest.main()
