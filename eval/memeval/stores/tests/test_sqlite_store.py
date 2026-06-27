"""Unit tests for :class:`memeval.stores.sqlite_store.SqliteVectorStore`. Owner: Brent.

v1 is stdlib-only: `sqlite3` storage + a deterministic char-n-gram feature-hashing
embedder + brute-force cosine. Real dense embeddings (Voyage/bge) + ANN (HNSW/FAISS)
are injected/lazy on the paid path and NOT exercised here. So these tests verify the
**plumbing** and the **fuzzy-match ordering** the offline embedder buys us — NOT true
dense semantic quality (that's a paid-path eval, deferred).

Run from `eval/`:  python3 -m unittest memeval.stores.tests.test_sqlite_store
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memeval.protocols import MemoryStore
from memeval.schema import MemoryItem
from memeval.stores.sqlite_store import (
    SQLITE_VEC_ANN_OVERFETCH,
    SQLITE_VEC_DIM,
    SQLITE_VEC_RECALL_AT_10_THRESHOLD,
    SqliteVectorStore,
    _HashingEmbedder,
    _embedder_accepts_input_type,
)


def _mk(item_id: str, content: str, *, timestamp: float = 0.0,
        relevancy: float = 1.0, tags=None) -> MemoryItem:
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy, tags=list(tags or []))


def _v(x: float, y: float = 0.0) -> list[float]:
    return [float(x), float(y)] + [0.0] * (SQLITE_VEC_DIM - 2)


class _DictEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def __call__(self, text: str, *, input_type=None) -> list[float]:
        return self.vectors[text]


def _sqlite_vec_store_or_skip(
    case: unittest.TestCase,
    path: str = ":memory:",
    *,
    embed=None,
    ann_overfetch: int = SQLITE_VEC_ANN_OVERFETCH,
) -> SqliteVectorStore:
    store = SqliteVectorStore(
        path,
        embed=embed or _DictEmbedder({"probe": _v(1.0)}),
        dim=SQLITE_VEC_DIM,
        vector_index="sqlite_vec",
        ann_overfetch=ann_overfetch,
        exact_rerank=True,
    )
    if store.vector_index != "sqlite_vec":
        reason = store.vector_index_status
        store.close()
        case.skipTest(reason)
    return store


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

    def test_iter_pages_empty_store_yields_nothing(self) -> None:
        """ADR-028 §2 PR #2g — empty store yields no pages."""
        s = self.store()
        self.assertEqual(list(s.iter_pages(page_size=10)), [])

    def test_iter_pages_single_page_smaller_than_page_size(self) -> None:
        """ADR-028 §2 PR #2g — items fitting in one page yield a single page."""
        s = self.store()
        s.write(_mk("a", "alpha"))
        s.write(_mk("b", "beta"))
        s.write(_mk("c", "gamma"))
        pages = list(s.iter_pages(page_size=10))
        self.assertEqual(len(pages), 1)
        self.assertEqual([it.item_id for it in pages[0]], ["a", "b", "c"])

    def test_iter_pages_partitions_at_page_size_boundary(self) -> None:
        """ADR-028 §2 PR #2g — 5 items at page_size=2 yields (2, 2, 1)."""
        s = self.store()
        for i in range(5):
            s.write(_mk(f"item-{i}", f"content-{i}"))
        pages = list(s.iter_pages(page_size=2))
        self.assertEqual([len(p) for p in pages], [2, 2, 1])
        flat_ids = [it.item_id for p in pages for it in p]
        self.assertEqual(flat_ids, [f"item-{i}" for i in range(5)])

    def test_iter_pages_invalid_page_size_raises(self) -> None:
        """ADR-028 §2 PR #2g — `page_size <= 0` raises ValueError."""
        s = self.store()
        with self.assertRaises(ValueError):
            list(s.iter_pages(page_size=0))
        with self.assertRaises(ValueError):
            list(s.iter_pages(page_size=-1))

    def test_iter_pages_concatenation_matches_all(self) -> None:
        """ADR-028 §2 PR #2g — flattening pages from iter_pages yields the
        same items in the same order as `all()`. The page-walk is a strict
        refinement of the materialize-all read; behavior parity is required."""
        s = self.store()
        for i in range(12):
            s.write(_mk(f"m{i}", f"text-{i}"))
        from_all = [it.item_id for it in s.all()]
        from_iter = [
            it.item_id for page in s.iter_pages(page_size=5) for it in page
        ]
        self.assertEqual(from_all, from_iter)

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


class SqliteVecOptionalTests(unittest.TestCase):
    def test_sqlite_vec_falls_back_to_brute_force_when_unavailable(self) -> None:
        emb = _DictEmbedder({"alpha": _v(1.0), "query": _v(1.0)})
        s = SqliteVectorStore(
            ":memory:",
            embed=emb,
            dim=SQLITE_VEC_DIM,
            vector_index="sqlite_vec",
            ann_overfetch=1,
        )
        try:
            self.assertIn(s.vector_index, {"sqlite_vec", "brute_force"})
            s.write(_mk("a", "alpha"))
            self.assertEqual(s.search("query", k=1)[0].item_id, "a")
        finally:
            s.close()

    def test_sqlite_vec_known_vector_ranking(self) -> None:
        emb = _DictEmbedder({
            "near": _v(1.0, 0.0),
            "mid": _v(0.8, 0.2),
            "far": _v(0.0, 1.0),
            "query": _v(1.0, 0.0),
        })
        s = _sqlite_vec_store_or_skip(self, embed=emb)
        try:
            s.write(_mk("far", "far"))
            s.write(_mk("mid", "mid"))
            s.write(_mk("near", "near"))
            self.assertEqual(
                [h.item_id for h in s.search("query", k=3)],
                ["near", "mid", "far"],
            )
        finally:
            s.close()

    def test_sqlite_vec_rowid_order_opposite_distance_order(self) -> None:
        emb = _DictEmbedder({
            "far": _v(0.0, 1.0),
            "near": _v(1.0, 0.0),
            "query": _v(1.0, 0.0),
        })
        s = _sqlite_vec_store_or_skip(self, embed=emb, ann_overfetch=1)
        try:
            s.write(_mk("far", "far"))
            s.write(_mk("near", "near"))
            self.assertEqual(s.search("query", k=1)[0].item_id, "near")
        finally:
            s.close()

    def test_sqlite_vec_overwrite_and_delete_refresh_index(self) -> None:
        emb = _DictEmbedder({
            "alpha": _v(1.0, 0.0),
            "beta": _v(0.0, 1.0),
            "other": _v(1.0, 0.0),
            "query": _v(1.0, 0.0),
        })
        s = _sqlite_vec_store_or_skip(self, embed=emb)
        try:
            s.write(_mk("target", "alpha"))
            self.assertEqual(s.search("query", k=1)[0].item_id, "target")
            s.write(_mk("target", "beta"))
            s.write(_mk("other", "other"))
            self.assertEqual(s.search("query", k=1)[0].item_id, "other")
            self.assertTrue(s.delete("other"))
            self.assertNotIn("other", [h.item_id for h in s.search("query", k=5)])
        finally:
            s.close()

    def test_sqlite_vec_as_of_filter_runs_inside_ann_query(self) -> None:
        emb = _DictEmbedder({
            "old": _v(0.9, 0.1),
            "future": _v(1.0, 0.0),
            "query": _v(1.0, 0.0),
        })
        s = _sqlite_vec_store_or_skip(self, embed=emb, ann_overfetch=1)
        try:
            s.write(_mk("old", "old", timestamp=10.0))
            s.write(_mk("future", "future", timestamp=20.0))
            ids = [h.item_id for h in s.search("query", k=1, as_of=15.0)]
            self.assertEqual(ids, ["old"])
        finally:
            s.close()

    def test_sqlite_vec_cross_process_peer_visibility(self) -> None:
        emb = _DictEmbedder({"alpha": _v(1.0, 0.0), "query": _v(1.0, 0.0)})
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "mem.db")
            s = _sqlite_vec_store_or_skip(self, path, embed=emb)
            try:
                s.write(_mk("alpha-id", "alpha"))
                eval_root = Path(__file__).resolve().parents[3]
                env = os.environ.copy()
                env["PYTHONPATH"] = str(eval_root)
                code = f"""
from memeval.schema import MemoryItem
from memeval.stores.sqlite_store import SQLITE_VEC_DIM, SqliteVectorStore
def v(x, y=0.0):
    return [float(x), float(y)] + [0.0] * (SQLITE_VEC_DIM - 2)
class E:
    def __call__(self, text, *, input_type=None):
        return {{"alpha": v(1.0), "query": v(1.0)}}[text]
s = SqliteVectorStore({path!r}, embed=E(), dim=SQLITE_VEC_DIM, vector_index="sqlite_vec")
assert s.vector_index == "sqlite_vec", s.vector_index_status
print(s.search("query", k=1)[0].item_id)
s.close()
"""
                proc = subprocess.run(
                    [sys.executable, "-c", code],
                    cwd=str(eval_root),
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(proc.stdout.strip(), "alpha-id")
            finally:
                s.close()

    def test_sqlite_vec_recall_matches_brute_force_on_minilm_vectors_when_available(self) -> None:
        from memeval.stores.embedders import SentenceTransformersEmbedder

        embed = SentenceTransformersEmbedder()
        try:
            embed.embed("availability probe", input_type="query")
        except RuntimeError as exc:
            self.skipTest(str(exc))
        from memeval.stores.tests import test_semantic_retrieval_evals as semantic

        exact = SqliteVectorStore(":memory:", embed=embed, dim=SQLITE_VEC_DIM)
        ann = None
        try:
            ann = _sqlite_vec_store_or_skip(self, embed=embed)
            for item in semantic.CORPUS:
                exact.write(item)
                ann.write(item)
            recalls: list[float] = []
            for case in semantic.SEMANTIC_CASES:
                exact_ids = [h.item_id for h in exact.search(case.query, k=10)]
                ann_ids = [h.item_id for h in ann.search(case.query, k=10)]
                if exact_ids:
                    recalls.append(len(set(exact_ids) & set(ann_ids)) / len(exact_ids))
            self.assertTrue(recalls)
            self.assertGreaterEqual(
                sum(recalls) / len(recalls),
                SQLITE_VEC_RECALL_AT_10_THRESHOLD,
            )
        finally:
            exact.close()
            if ann is not None:
                ann.close()


if __name__ == "__main__":
    unittest.main()
