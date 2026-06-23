"""Rerankers for retrieval — owner: Brent (@bgibson1618).

A retriever (vector ANN / lexical BM25) returns the top-N candidates by a cheap similarity; a
**reranker** then re-scores those candidates against the query with a stronger cross-encoder and keeps
the best top-k. PRD §7.1 commits to a Voyage/Cohere reranker over the top ~50 ANN hits. This module
ships that component, mirroring the :mod:`memeval.stores.embedders` design:

* :class:`MockReranker` — deterministic, offline (stdlib token-overlap); records its calls for tests.
* :class:`VoyageReranker` — the real reranker over Voyage's rerank REST endpoint via stdlib
  :mod:`urllib` (no SDK, no ``numpy``). **Paid at call time, not import time:** importing this module
  touches no network and needs no key; only calling :class:`VoyageReranker` does. A missing key raises
  a clear :class:`RuntimeError` rather than silently degrading (which would mislabel an offline run as
  a paid retrieval-quality run). Basic exponential backoff retries 429 / 5xx + transient network errors.
* :func:`rerank_items` — apply a reranker to a list of :class:`~memeval.schema.RetrievedItem`, returning
  the top-k re-scored and re-ranked (``rank`` reset 0..k-1).
* :class:`RerankedStore` — a :class:`~memeval.protocols.MemoryStore` facade that over-fetches
  ``rerank_top_n`` candidates from an inner store and reranks them to ``k`` (the "rerank over the top
  ~50" realization). It composes with any backend, including ``RouterStore``.

A reranker is callable as ``reranker(query, documents, *, top_k=None) -> list[(orig_index, score)]``
sorted by descending relevance (truncated to ``top_k`` when given) — the shape of the rerank API.

Offline note (the D019/D020 lesson): an offline lexical reranker can only demonstrate the MECHANISM +
reordering. The retrieval-quality LIFT of a real cross-encoder reranker is a **captained** run (real
:class:`VoyageReranker`), never in CI. The offline default is NO rerank.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from ..protocols import MemoryStore
from ..schema import RetrievedItem

# Voyage rerank REST endpoint.
_VOYAGE_RERANK_ENDPOINT = "https://api.voyageai.com/v1/rerank"
# HTTP statuses worth a retry: rate-limit + transient server errors.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _tokens(text: str) -> list:
    """Lowercased whitespace tokens of ``text`` (the offline relevance signal)."""
    return (text or "").lower().split()


class MockReranker:
    """Deterministic offline reranker: scores each document by token-overlap (Jaccard) with the query.

    Returns ``[(original_index, score), ...]`` sorted by descending score, ties broken by original
    index (stable). Records every ``(query, documents, top_k)`` it was called with in :attr:`calls` so
    a test can assert it was consulted. Lexical only — enough to exercise the reranker seam offline; the
    real semantic re-scoring is :class:`VoyageReranker` (captained).
    """

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, query: str, documents: list, *, top_k: Optional[int] = None) -> list:
        self.calls.append((query, list(documents), top_k))
        q = set(_tokens(query))
        scored: list = []
        for i, doc in enumerate(documents):
            d = set(_tokens(doc))
            union = q | d
            scored.append((i, (len(q & d) / len(union)) if union else 0.0))
        scored.sort(key=lambda t: (-t[1], t[0]))  # desc score, stable by index
        return scored if top_k is None else scored[:top_k]


class VoyageReranker:
    """Real reranker backed by Voyage ``rerank-2.5`` over stdlib ``urllib``.

    Callable as ``reranker(query, documents, *, top_k=None) -> list[(orig_index, relevance_score)]``,
    sorted by descending relevance. POSTs ``{model, query, documents, top_k?}`` to the Voyage rerank
    endpoint and parses ``data[].{index, relevance_score}``. The API key is read from
    ``os.environ[api_key_env]`` at call time (never stored, never logged); a missing key raises
    :class:`RuntimeError`. Importing/constructing touches no network — only calling does.
    """

    def __init__(
        self,
        model: str = "rerank-2.5",
        api_key_env: str = "VOYAGE_API_KEY",
        *,
        endpoint: str = _VOYAGE_RERANK_ENDPOINT,
        max_retries: int = 3,
        backoff: float = 0.5,
        timeout: float = 30.0,
        sleeper: Optional[Any] = None,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.endpoint = endpoint
        self.max_retries = max_retries
        self.backoff = backoff
        self.timeout = timeout
        self._sleeper = sleeper or time.sleep

    def __call__(self, query: str, documents: list, *, top_k: Optional[int] = None) -> list:
        """Rerank ``documents`` against ``query``; return ``[(orig_index, score)]`` best-first.

        Raises :class:`RuntimeError` if the API key is unset, on a non-retryable / exhausted API
        error, or if the response shape is unexpected. An empty ``documents`` makes no request.
        """
        docs = list(documents)
        if not docs:
            return []
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"{self.api_key_env} is not set; VoyageReranker needs a Voyage API key "
                "(no offline fallback — inject MockReranker for offline use)."
            )
        payload: dict[str, Any] = {"model": self.model, "query": query, "documents": docs}
        if top_k is not None:
            payload["top_k"] = top_k
        data = self._post(payload, api_key)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise RuntimeError("Voyage rerank response shape unexpected")
        out: list = []
        seen: set = set()
        for row in rows:
            idx, score = row.get("index"), row.get("relevance_score")
            # bool is an int subclass — exclude it; index must be a unique, in-range row.
            if (isinstance(idx, bool) or not isinstance(idx, int)
                    or not isinstance(score, (int, float)) or isinstance(score, bool)
                    or not (0 <= idx < len(docs)) or idx in seen):
                raise RuntimeError("Voyage rerank response shape unexpected")
            seen.add(idx)
            out.append((idx, float(score)))
        out.sort(key=lambda t: (-t[1], t[0]))  # best-first; defensive (API already sorts)
        return out

    # -- transport (mirrors VoyageEmbedder._post) --------------------------
    def _post(self, payload: dict, api_key: str) -> dict:
        """POST ``payload`` as JSON with retry/backoff; return the parsed response.

        The key rides in the ``Authorization`` header only and is never logged. ``urlopen`` is
        referenced through the module so tests can monkeypatch it with a canned response.
        """
        body = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        last_exc: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                exc.close()
                if exc.code in _RETRYABLE_STATUS and attempt < self.max_retries:
                    self._sleeper(self.backoff * (2 ** attempt))
                    continue
                raise RuntimeError(f"Voyage rerank API error (HTTP {exc.code})") from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                # socket.timeout IS TimeoutError on 3.10+ and is NOT a URLError subclass, so list it
                # explicitly: a request timeout backs off, retries, and (exhausted) wraps as RuntimeError.
                last_exc = exc
                if attempt < self.max_retries:
                    self._sleeper(self.backoff * (2 ** attempt))
                    continue
                raise RuntimeError("Voyage rerank API request failed") from exc
        raise RuntimeError("Voyage rerank API request failed") from last_exc


def rerank_items(query: str, items: list, *, reranker: Any, k: int = 5) -> list:
    """Rerank a list of :class:`RetrievedItem` by ``reranker``; return the top-``k`` re-scored.

    Each returned item carries the reranker's relevance ``score`` and a fresh ``rank`` (0..k-1, best
    first); the underlying :class:`~memeval.schema.MemoryItem` is preserved unchanged. ``items`` is the
    candidate set (e.g. the retriever's top-N); an empty set returns ``[]``.
    """
    if not items or k <= 0:
        return []
    docs = [it.item.content or "" for it in items]
    ranked = reranker(query, docs, top_k=k)
    out: list = []
    seen: set = set()
    for new_rank, (idx, score) in enumerate(ranked):
        # Don't trust a reranker's index map blindly: a bad/duplicate index must fail loud, not
        # IndexError or silently duplicate a candidate.
        if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(items)):
            raise ValueError(
                f"reranker returned out-of-range index {idx!r} for {len(items)} candidates")
        if idx in seen:
            raise ValueError(f"reranker returned duplicate index {idx!r}")
        seen.add(idx)
        src = items[idx]
        out.append(RetrievedItem(item=src.item, score=float(score), rank=new_rank))
    return out


class RerankedStore:
    """A :class:`~memeval.protocols.MemoryStore` facade that reranks an inner store's top-N to top-k.

    ``search`` over-fetches ``max(k, rerank_top_n)`` candidates from ``inner`` (the cheap retriever),
    then applies ``reranker`` to keep the best ``k`` — the PRD's "reranker over the top ~50 ANN hits".
    ``write`` / ``get`` / ``all`` delegate straight to ``inner``. Composes with any backend (e.g. wrap a
    ``SqliteVectorStore`` or a ``RouterStore``). Offline, ``reranker`` is a :class:`MockReranker`; the
    real retrieval-quality lift uses a :class:`VoyageReranker` (captained).
    """

    def __init__(self, inner: MemoryStore, reranker: Any, *, rerank_top_n: int = 50) -> None:
        self._inner = inner
        self._reranker = reranker
        self._rerank_top_n = rerank_top_n

    def write(self, item) -> None:
        self._inner.write(item)

    def get(self, item_id: str):
        return self._inner.get(item_id)

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs) -> list:
        # Non-positive k returns nothing (matching the stores) — and short-circuits BEFORE the inner
        # fetch + the (possibly paid) reranker call, so a k<=0 query does no work.
        if k <= 0:
            return []
        fetch = max(k, self._rerank_top_n)
        candidates = self._inner.search(query, k=fetch, as_of=as_of, **kwargs)
        return rerank_items(query, candidates, reranker=self._reranker, k=k)

    def all(self) -> list:
        return self._inner.all()

    def delete(self, item_id: str) -> bool:
        """Delete ``item_id`` from the inner store (reranking is read-only; delete just delegates)."""
        return self._inner.delete(item_id)


__all__ = ["MockReranker", "VoyageReranker", "rerank_items", "RerankedStore"]
