"""Unit tests for :mod:`memeval.stores.embedders`. Owner: Brent.

MOCK-ONLY — no live Voyage calls, no API key, no network. Coverage:

* :class:`MockEmbedder` write→search round-trip through ``SqliteVectorStore(embed=...)``.
* The Voyage query/document **asymmetry** flows through the store's embed seam:
  ``write`` embeds ``input_type="document"``, ``search`` embeds ``input_type="query"``
  (asserted via the MockEmbedder's recorded calls).
* :class:`VoyageEmbedder` guards: unset key raises a clear ``RuntimeError``; the request
  payload is shaped correctly and the response is parsed (``urllib.request.urlopen``
  monkeypatched with a canned response — NO real network); retry/backoff on 429/5xx.
* dim consistency + the rebuild contract (mixing dims raises).

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_embedders
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from memeval.schema import MemoryItem
from memeval.stores.embedders import (
    MockEmbedder,
    VoyageEmbedder,
    _hash_vector,
    rebuild_store,
)
from memeval.stores.sqlite_store import SqliteVectorStore


class _VoyageEnvTestCase(unittest.TestCase):
    """Base for every test that reads ``VOYAGE_API_KEY`` or monkeypatches ``urlopen``.

    Centralizes env isolation so no test leaks a key (or its absence) into another —
    the kind of order/env bleed that produced a one-time, non-reproducible failure in
    a combined run. ``setUp`` snapshots and removes any ambient key (so guard tests see
    a truly unset key regardless of run order); ``tearDown`` unconditionally restores
    the snapshot. Tests that need a key call :meth:`set_key`; the restore is automatic.
    """

    def setUp(self) -> None:
        self._saved_voyage_key = os.environ.pop("VOYAGE_API_KEY", None)

    def tearDown(self) -> None:
        if self._saved_voyage_key is not None:
            os.environ["VOYAGE_API_KEY"] = self._saved_voyage_key
        else:
            os.environ.pop("VOYAGE_API_KEY", None)

    def set_key(self, value: str = "vk-test") -> None:
        """Set ``VOYAGE_API_KEY`` for this test; ``tearDown`` restores the prior value."""
        os.environ["VOYAGE_API_KEY"] = value


def _mk(item_id: str, content: str, *, timestamp: float = 0.0,
        relevancy: float = 1.0) -> MemoryItem:
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy)


# --------------------------------------------------------------------------- #
# A fake urlopen: context-manager response with a canned JSON body, and a
# recorder for the Request so tests can assert the outgoing payload.
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._buf = BytesIO(body)

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def _voyage_response(vectors: list) -> bytes:
    """Serialize a Voyage-shaped embeddings response for ``vectors`` (one per input)."""
    return json.dumps({
        "object": "list",
        "data": [{"object": "embedding", "embedding": v, "index": i}
                 for i, v in enumerate(vectors)],
        "model": "voyage-3-large",
        "usage": {"total_tokens": 7},
    }).encode("utf-8")


class _UrlopenPatch:
    """Context manager: swap ``urllib.request.urlopen`` for ``handler``; restore after.

    ``handler(request, timeout=...)`` receives the real :class:`urllib.request.Request`
    so a test can assert the URL, headers, and JSON body, then return a response (or
    raise) of its choosing. No real network is ever touched.
    """

    def __init__(self, handler) -> None:
        self._handler = handler
        self._orig = None

    def __enter__(self):
        self._orig = urllib.request.urlopen
        urllib.request.urlopen = self._handler
        return self

    def __exit__(self, *exc) -> None:
        urllib.request.urlopen = self._orig


class _PerturbingMockEmbedder:
    """Offline embedder that embeds documents and queries DIFFERENTLY — like real Voyage,

    where a document and its query land at *nearby but distinct* points in the same
    space. The vector for ``input_type="query"`` is a gentle, deterministic reweighting
    of the document vector for the same text: enough to genuinely diverge, small enough
    to leave nearest-neighbor ranking intact. Lets a test prove the store's
    write('document') -> search('query') round-trip is robust to that divergence (the
    real retrieval-quality property), with no API key and no network.
    """

    def __init__(self, dim: int = 256, n: int = 3) -> None:
        self.dim, self.n = dim, n

    def __call__(self, text: str, *, input_type=None) -> list:
        vec = _hash_vector(text, self.dim, self.n)
        if input_type == "query":
            vec = [v * (0.85 if (i % 5 == 0) else 1.0) for i, v in enumerate(vec)]
        return vec


# --------------------------------------------------------------------------- #
# MockEmbedder + the store seam (offline, deterministic)
# --------------------------------------------------------------------------- #
class MockEmbedderStoreTests(unittest.TestCase):
    def test_round_trip_through_store(self) -> None:
        s = SqliteVectorStore(":memory:", embed=MockEmbedder())
        s.write(_mk("cfg", "configuration settings for the service"))
        s.write(_mk("cat", "the cat sat on the mat"))
        hits = s.search("configuration options", k=2)
        self.assertTrue(hits)
        self.assertEqual(hits[0].item_id, "cfg")
        self.assertEqual(hits[0].rank, 0)
        self.assertGreater(hits[0].tokens, 0)

    def test_round_trip_robust_to_doc_query_vector_divergence(self) -> None:
        # Real Voyage embeds a doc and a query DIFFERENTLY; the round-trip must still
        # retrieve the doc-embedded item from a query-embedded query. Use an embedder
        # whose doc/query vectors genuinely diverge and assert retrieval still holds.
        emb = _PerturbingMockEmbedder(dim=256)
        same = "configuration options"
        self.assertNotEqual(  # doc vs query vectors for the SAME text genuinely differ
            emb(same, input_type="document"), emb(same, input_type="query"))
        s = SqliteVectorStore(":memory:", embed=emb)
        s.write(_mk("cfg", "configuration settings for the service"))  # stored as 'document'
        s.write(_mk("cat", "the cat sat on the mat"))
        hits = s.search("configuration options", k=2)  # query embedded as 'query'
        self.assertTrue(hits)
        self.assertEqual(hits[0].item_id, "cfg")  # doc-embedded item still retrieved first

    def test_input_type_asymmetry_document_vs_query(self) -> None:
        mock = MockEmbedder()
        s = SqliteVectorStore(":memory:", embed=mock)
        s.write(_mk("a", "alpha content"))
        s.write(_mk("b", "beta content"))
        # writes so far embed as documents; nothing has queried yet
        self.assertEqual(mock.input_types, ["document", "document"])
        mock.reset()
        s.search("alpha", k=2)
        self.assertEqual(mock.input_types, ["query"])  # the query embeds as a query

    def test_dimension_is_consistent(self) -> None:
        mock = MockEmbedder(dim=1024)
        self.assertEqual(len(mock("hello world")), 1024)
        self.assertEqual(len(mock("hello world", input_type="query")), 1024)
        # input_type does not change the vector (same text -> same vector)
        self.assertEqual(mock("same text", input_type="document"),
                         mock("same text", input_type="query"))

    def test_records_text_and_input_type(self) -> None:
        mock = MockEmbedder()
        mock("x", input_type="document")
        mock("y", input_type="query")
        mock("z")  # default omitted -> None recorded
        self.assertEqual(mock.calls, [("x", "document"), ("y", "query"), ("z", None)])


# --------------------------------------------------------------------------- #
# The rebuild contract: switching embedders changes the dim -> mixing raises
# --------------------------------------------------------------------------- #
class RebuildContractTests(unittest.TestCase):
    def test_mixing_dims_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "mem.db")
            s = SqliteVectorStore(path, embed=MockEmbedder(dim=1024))
            s.write(_mk("m1", "configuration settings"))
            s.close()
            # Reopen the SAME db with a different-dim embedder: the stored 1024-dim
            # vectors can't be compared with an 8-dim query -> _cosine fails loud.
            s2 = SqliteVectorStore(path, embed=MockEmbedder(dim=8))
            with self.assertRaises(ValueError):
                s2.search("configuration", k=1)
            s2.close()

    def test_rebuild_store_reindexes_under_new_embedder(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = str(Path(d) / "src.db")
            dest = str(Path(d) / "dest.db")
            old = SqliteVectorStore(src, embed=MockEmbedder(dim=8))
            old.write(_mk("m1", "configuration settings"))
            old.write(_mk("m2", "the cat sat on the mat"))
            items = old.all()
            old.close()
            new = rebuild_store(items, dest, embed=MockEmbedder(dim=1024),
                                embed_model="voyage-3-large")
            # The rebuilt store is queryable at the new dimension and round-trips.
            self.assertEqual(new.embed_model, "voyage-3-large")
            hits = new.search("configuration", k=2)
            self.assertTrue(hits and hits[0].item_id == "m1")
            self.assertEqual(len(new._embed_text("anything", "document")), 1024)
            new.close()


# --------------------------------------------------------------------------- #
# VoyageEmbedder guards — NO network (urlopen monkeypatched / never called)
# --------------------------------------------------------------------------- #
class VoyageEmbedderGuardTests(_VoyageEnvTestCase):
    # Env isolation (save/pop/restore VOYAGE_API_KEY) is inherited from
    # _VoyageEnvTestCase so no test bleeds a key into another, even in a combined run.

    def test_missing_key_raises_runtimeerror(self) -> None:
        # setUp already removed any ambient key; assert the unset-key guard fires.
        emb = VoyageEmbedder()
        with self.assertRaises(RuntimeError) as ctx:
            emb("hello")
        self.assertIn("VOYAGE_API_KEY", str(ctx.exception))

    def test_payload_shape_and_parse(self) -> None:
        self.set_key("vk-test-DO-NOT-LOG")
        captured: dict = {}

        def handler(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["auth"] = request.get_header("Authorization")
            captured["content_type"] = request.get_header("Content-type")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse(_voyage_response([[0.1, 0.2, 0.3, 0.4]]))

        emb = VoyageEmbedder(dim=4)
        with _UrlopenPatch(handler):
            vec = emb("a stored fact", input_type="document")

        self.assertEqual(vec, [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/v1/embeddings"))
        self.assertEqual(captured["auth"], "Bearer vk-test-DO-NOT-LOG")
        self.assertEqual(captured["content_type"], "application/json")
        body = captured["body"]
        self.assertEqual(body["model"], "voyage-3-large")
        self.assertEqual(body["input"], ["a stored fact"])
        self.assertEqual(body["input_type"], "document")
        self.assertEqual(body["output_dimension"], 4)

    def test_query_input_type_sent(self) -> None:
        self.set_key()
        captured: dict = {}

        def handler(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(_voyage_response([[1.0, 0.0]]))

        emb = VoyageEmbedder(dim=2)
        with _UrlopenPatch(handler):
            emb("a search", input_type="query")
        self.assertEqual(captured["body"]["input_type"], "query")

    def test_default_input_type_is_document(self) -> None:
        self.set_key()
        captured: dict = {}

        def handler(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(_voyage_response([[1.0, 0.0]]))

        emb = VoyageEmbedder(dim=2)
        with _UrlopenPatch(handler):
            emb("no explicit input_type")  # falls back to input_type_default
        self.assertEqual(captured["body"]["input_type"], "document")

    def test_batch_preserves_order_by_index(self) -> None:
        self.set_key()

        def handler(request, timeout=None):
            # Return rows out of order; the embedder must re-sort by ``index``.
            body = json.dumps({"data": [
                {"embedding": [2.0], "index": 1},
                {"embedding": [1.0], "index": 0},
            ]}).encode("utf-8")
            return _FakeResponse(body)

        emb = VoyageEmbedder(dim=1)
        with _UrlopenPatch(handler):
            vecs = emb.embed_batch(["first", "second"], input_type="document")
        self.assertEqual(vecs, [[1.0], [2.0]])

    def test_empty_batch_makes_no_request(self) -> None:
        def handler(request, timeout=None):  # pragma: no cover - must not be called
            raise AssertionError("urlopen must not be called for an empty batch")

        emb = VoyageEmbedder()
        with _UrlopenPatch(handler):
            self.assertEqual(emb.embed_batch([]), [])

    def test_retry_on_5xx_then_succeeds(self) -> None:
        self.set_key()
        attempts = {"n": 0}
        slept: list = []

        def handler(request, timeout=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise urllib.error.HTTPError(
                    request.full_url, 503, "Service Unavailable",
                    hdrs=None, fp=BytesIO(b"{}"))
            return _FakeResponse(_voyage_response([[0.5, 0.5]]))

        emb = VoyageEmbedder(dim=2, max_retries=3, sleeper=lambda s: slept.append(s))
        with _UrlopenPatch(handler):
            vec = emb("retry please")
        self.assertEqual(vec, [0.5, 0.5])
        self.assertEqual(attempts["n"], 3)        # two failures, third succeeds
        self.assertEqual(len(slept), 2)           # backed off before each retry
        self.assertTrue(all(s >= 0 for s in slept))

    def test_non_retryable_4xx_raises(self) -> None:
        self.set_key()
        attempts = {"n": 0}

        def handler(request, timeout=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", hdrs=None, fp=BytesIO(b"{}"))

        emb = VoyageEmbedder(sleeper=lambda s: None)
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                emb("bad input")
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(attempts["n"], 1)        # 4xx is not retried

    def test_retry_exhausted_raises(self) -> None:
        self.set_key()
        attempts = {"n": 0}

        def handler(request, timeout=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests",
                hdrs=None, fp=BytesIO(b"{}"))

        emb = VoyageEmbedder(max_retries=2, sleeper=lambda s: None)
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError):
                emb("rate limited")
        self.assertEqual(attempts["n"], 3)        # initial try + 2 retries

    def test_timeout_is_retried_then_wrapped(self) -> None:
        # REGRESSION: socket.timeout IS TimeoutError on 3.10+ and is NOT a URLError
        # subclass, so a request timeout (the exact transient the 30s timeout bounds)
        # must still back off, retry, and — when exhausted — re-raise as the documented
        # RuntimeError, never escape raw as a socket.timeout/TimeoutError.
        self.set_key()
        attempts = {"n": 0}
        slept: list = []

        def handler(request, timeout=None):
            attempts["n"] += 1
            raise socket.timeout("timed out")   # is TimeoutError on 3.10+

        emb = VoyageEmbedder(max_retries=2, sleeper=lambda s: slept.append(s))
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                emb("slow endpoint")
        self.assertEqual(attempts["n"], 3)            # initial try + 2 retries
        self.assertEqual(len(slept), 2)               # backed off before each retry
        self.assertIsInstance(ctx.exception.__cause__, TimeoutError)  # original preserved
        # the timeout reason is NOT surfaced in the wrapped message
        self.assertNotIn("timed out", str(ctx.exception))

    def test_malformed_response_non_dict_raises_runtimeerror(self) -> None:
        # A non-dict response body (would AttributeError on ``.get``) must surface as
        # the documented RuntimeError, naming no body content.
        self.set_key()

        def handler(request, timeout=None):
            return _FakeResponse(json.dumps(["not", "a", "dict"]).encode("utf-8"))

        emb = VoyageEmbedder(dim=2)
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                emb("anything")
        self.assertIn("shape", str(ctx.exception).lower())

    def test_malformed_response_missing_embedding_raises_runtimeerror(self) -> None:
        # A row missing the ``embedding`` key (would KeyError) must surface as the
        # documented RuntimeError, not an undocumented KeyError.
        self.set_key()

        def handler(request, timeout=None):
            body = json.dumps({"data": [{"index": 0}]}).encode("utf-8")  # no 'embedding'
            return _FakeResponse(body)

        emb = VoyageEmbedder(dim=2)
        with _UrlopenPatch(handler):
            with self.assertRaises(RuntimeError) as ctx:
                emb("anything")
        self.assertIn("shape", str(ctx.exception).lower())

    def test_no_network_at_import_or_construction(self) -> None:
        # Constructing the embedder must not require a key or touch the network.
        # (setUp already removed any ambient key.)
        def handler(request, timeout=None):  # pragma: no cover - must not be called
            raise AssertionError("construction must not call the network")

        with _UrlopenPatch(handler):
            emb = VoyageEmbedder()  # no error, no request
        self.assertEqual(emb.model, "voyage-3-large")
        self.assertEqual(emb.dim, 1024)


# --------------------------------------------------------------------------- #
# Voyage as the store's embedder (still no network — urlopen monkeypatched)
# --------------------------------------------------------------------------- #
class VoyageThroughStoreTests(_VoyageEnvTestCase):
    def test_store_threads_input_type_to_voyage(self) -> None:
        self.set_key()
        seen: list = []

        def handler(request, timeout=None):
            body = json.loads(request.data.decode("utf-8"))
            seen.append(body["input_type"])
            # 4-dim canned vector; write+search both go through here
            return _FakeResponse(_voyage_response([[0.1, 0.2, 0.3, 0.4]]))

        emb = VoyageEmbedder(dim=4)
        with _UrlopenPatch(handler):
            s = SqliteVectorStore(":memory:", embed=emb, embed_model="voyage-3-large")
            s.write(_mk("m1", "a stored fact"))
            s.search("a query", k=1)
            s.close()
        self.assertIn("document", seen)   # write embedded as a document
        self.assertIn("query", seen)      # search embedded as a query


if __name__ == "__main__":
    unittest.main()
