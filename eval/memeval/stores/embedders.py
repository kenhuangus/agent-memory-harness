"""Real / mock dense embedders for the vector store — owner: Brent (@bgibson1618).

The offline default in :mod:`memeval.stores.sqlite_store` is the stdlib char-n-gram
hashing embedder. The *paid* path injects a real dense embedder via
``SqliteVectorStore(embed=...)``. This module ships real adapters —
:class:`VoyageEmbedder` (Voyage ``voyage-3-large``, output dimension 1024) and
:class:`SentenceTransformersEmbedder` (local MiniLM, output dimension 384) — plus a
deterministic offline :class:`MockEmbedder` for unit tests and local dev.

Two design points carried from the PR3 architecture:

* **No SDK.** The Voyage adapter speaks the embeddings REST endpoint over stdlib
  :mod:`urllib` — no ``voyageai`` / ``requests`` / ``numpy``. The paid path is paid
  at *call* time, not at import time: importing this module touches no network and
  needs no key.
* **Query/document asymmetry.** Voyage embeds a stored item and a search query
  differently (``input_type="document"`` vs ``"query"``) — a real retrieval-quality
  win. Both embedders accept an optional keyword-only ``input_type`` so the store can
  carry that distinction through the embed seam. :class:`MockEmbedder` *records* the
  ``input_type`` it was called with so tests can assert document-vs-query without a key.

Reindex note: switching embedders changes the vector dimension (e.g. the 256-dim
hashing default vs. Voyage's 1024). A store's vectors are JSON-stored at write time,
and :func:`memeval.stores.sqlite_store._cosine` raises on a dimension mismatch — so a
vector DB built with one embedder *cannot* be queried with another. Switching
embedders requires a **fresh store**; :func:`rebuild_store` re-embeds a set of items
into a new store under a chosen embedder.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

# Voyage embeddings REST endpoint (OpenAI-compatible request/response shape).
_VOYAGE_ENDPOINT = "https://api.voyageai.com/v1/embeddings"
# HTTP statuses worth a retry: rate-limit + transient server errors.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Cap any single backoff sleep so a hostile/huge ``Retry-After`` can't hang a run.
_MAX_BACKOFF_SECONDS = 60.0


def _retry_after_seconds(exc: "urllib.error.HTTPError") -> Optional[float]:
    """Parse a ``Retry-After`` header (delta-seconds form) into a capped float, else None.

    Voyage returns ``Retry-After`` on 429/503; honoring it backs off exactly as long
    as the server asks instead of guessing. Only the integer/float delta-seconds form
    is handled (the HTTP-date form is rare here and not worth the parser); anything
    unparseable falls through to exponential backoff.
    """
    try:
        raw = exc.headers.get("Retry-After") if exc.headers else None
    except Exception:
        return None
    if not raw:
        return None
    try:
        secs = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if secs < 0:
        return None
    return min(secs, _MAX_BACKOFF_SECONDS)
_MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_MINILM_DIM = 384
_MINILM_CACHE_ENV = "MEMEVAL_EMBED_MODEL_CACHE"
_MODEL_DOWNLOAD_ENV = "MEMEVAL_ALLOW_MODEL_DOWNLOAD"


def _hash_vector(text: str, dim: int, n: int = 3) -> list:
    """Deterministic L2-normalized char-n-gram feature-hash of ``text``.

    Same algorithm as the store's offline ``_HashingEmbedder`` but standalone (this
    module stays independent of the store internals). Gives fuzzy/morphological
    similarity with no model or dependency — enough for an offline write→search
    round-trip in tests.
    """
    vec = [0.0] * dim
    s = (text or "").strip().lower()
    grams = [s[i : i + n] for i in range(len(s) - n + 1)]
    if not grams and s:
        grams = [s]
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class MockEmbedder:
    """Deterministic, offline embedder that records the ``input_type`` it saw.

    Produces a stable char-n-gram feature-hash vector (so a write→search round-trip
    through :class:`~memeval.stores.sqlite_store.SqliteVectorStore` retrieves the
    right item) while *recording* every ``(text, input_type)`` it was called with in
    :attr:`calls`. That lets a test assert the store embeds stored items as
    ``"document"`` and the query as ``"query"`` — the Voyage asymmetry — with no API
    key and no network. The vector itself is independent of ``input_type`` (so the
    same text embeds identically as a document or a query), which keeps the round-trip
    correct while still exercising the seam.
    """

    def __init__(self, dim: int = 1024, n: int = 3) -> None:
        self.dim, self.n = dim, n
        self.calls: list[tuple[str, Optional[str]]] = []

    def __call__(self, text: str, *, input_type: Optional[str] = None) -> list:
        self.calls.append((text, input_type))
        return _hash_vector(text, self.dim, self.n)

    @property
    def input_types(self) -> list:
        """The ``input_type`` of each recorded call, in order (``None`` if omitted)."""
        return [it for _, it in self.calls]

    def reset(self) -> None:
        """Forget all recorded calls (handy between assertions in one test)."""
        self.calls.clear()


class VoyageEmbedder:
    """Real dense embedder backed by Voyage ``voyage-3-large`` over stdlib ``urllib``.

    Callable as ``embedder(text, *, input_type=None) -> list[float]``; embeds a list
    in one request via :meth:`embed_batch`. POSTs ``{model, input, input_type,
    output_dimension}`` to the Voyage embeddings REST endpoint and parses the dense
    vectors out of ``data[].embedding`` (re-ordered by ``index``). A basic
    exponential backoff retries 429 / 5xx and transient network errors.

    The API key is read from ``os.environ[api_key_env]`` **at call time** (never
    stored, never logged); a missing key raises a clear :class:`RuntimeError` rather
    than silently falling back to an offline embedder (that would mislabel an offline
    run as a paid retrieval-quality run). Importing this class touches no network and
    needs no key — only calling it does.

    ``input_type`` carries Voyage's query/document asymmetry: the store passes
    ``"document"`` for stored items and ``"query"`` for the search query. When a call
    omits it, ``input_type_default`` ("document") is used.
    """

    def __init__(
        self,
        model: str = "voyage-3-large",
        dim: int = 1024,
        input_type_default: Optional[str] = "document",
        api_key_env: str = "VOYAGE_API_KEY",
        *,
        endpoint: str = _VOYAGE_ENDPOINT,
        max_retries: int = 3,
        backoff: float = 0.5,
        timeout: float = 30.0,
        sleeper: Optional[Any] = None,
    ) -> None:
        self.model = model
        self.dim = dim
        self.input_type_default = input_type_default
        self.api_key_env = api_key_env
        self.endpoint = endpoint
        self.max_retries = max_retries
        self.backoff = backoff
        self.timeout = timeout
        self._sleeper = sleeper or time.sleep

    # -- embed seam --------------------------------------------------------
    def __call__(self, text: str, *, input_type: Optional[str] = None) -> list:
        """Embed a single ``text``; returns its ``dim``-length dense vector."""
        return self.embed_batch([text], input_type=input_type)[0]

    def embed_batch(self, texts: list, *, input_type: Optional[str] = None) -> list:
        """Embed a list of ``texts`` in one request, preserving input order.

        Raises :class:`RuntimeError` if the API key is unset, on a non-retryable /
        exhausted API error, or if the response shape is unexpected.
        """
        if not texts:
            return []
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"{self.api_key_env} is not set; VoyageEmbedder needs a Voyage API key "
                "(no offline fallback — inject MockEmbedder/_HashingEmbedder for offline use)."
            )
        chosen = input_type if input_type is not None else self.input_type_default
        payload: dict[str, Any] = {
            "model": self.model,
            "input": list(texts),
            "output_dimension": self.dim,
        }
        if chosen is not None:
            payload["input_type"] = chosen
        data = self._post(payload, api_key)
        # Guard the response shape: a non-dict body (AttributeError on ``.get``) or a
        # row missing ``embedding`` (KeyError) must surface as the documented
        # RuntimeError, not an undocumented exception. The message names no field/body
        # so nothing from the response is leaked.
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise RuntimeError("Voyage response shape unexpected")
        rows = sorted(rows, key=lambda r: r.get("index", 0))
        if len(rows) != len(texts):
            raise RuntimeError(
                f"Voyage returned {len(rows)} embeddings for {len(texts)} inputs"
            )
        vectors: list = []
        for row in rows:
            embedding = row.get("embedding")
            if not isinstance(embedding, list):
                raise RuntimeError("Voyage response shape unexpected")
            vectors.append(list(embedding))
        return vectors

    # -- transport ---------------------------------------------------------
    def _post(self, payload: dict, api_key: str) -> dict:
        """POST ``payload`` as JSON with retry/backoff; return the parsed response.

        The key rides in the ``Authorization`` header only and is never logged. The
        ``urllib`` call is referenced through the module so tests can monkeypatch
        ``urllib.request.urlopen`` with a canned response (no real network).
        """
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                self.endpoint, data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_exc = exc
                retry_after = _retry_after_seconds(exc)  # read header BEFORE close()
                exc.close()  # we never read the error body; release the connection now
                if exc.code in _RETRYABLE_STATUS and attempt < self.max_retries:
                    # Honor the server's Retry-After when given (exact rate-limit wait);
                    # otherwise exponential backoff. Add jitter either way so concurrent
                    # embedders don't retry in lockstep and re-trigger the limit.
                    base = retry_after if retry_after is not None else self.backoff * (2 ** attempt)
                    self._sleeper(min(_MAX_BACKOFF_SECONDS, base) + random.uniform(0, self.backoff))
                    continue
                # Non-retryable status (or retries exhausted): fail loud. The body is
                # not surfaced to avoid leaking anything sensitive from the request.
                raise RuntimeError(f"Voyage API error (HTTP {exc.code})") from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                # Transient network failure. ``socket.timeout`` IS ``TimeoutError`` on
                # 3.10+ and is NOT a ``URLError`` subclass, so a request timeout (the
                # exact transient the ``timeout`` bounds) would otherwise escape raw,
                # neither retried nor wrapped — list it explicitly so it backs off and,
                # when exhausted, re-raises as the documented RuntimeError. The reason
                # is dropped from the message (defense-in-depth) and preserved on
                # ``__cause__`` for debugging.
                last_exc = exc
                if attempt < self.max_retries:
                    self._sleeper(self.backoff * (2 ** attempt))
                    continue
                raise RuntimeError("Voyage API request failed") from exc
        # Loop only exits via return/raise above; this guards the impossible path.
        raise RuntimeError("Voyage API request failed") from last_exc


class SentenceTransformersEmbedder:
    """Local MiniLM embedder for opt-in semantic eval/profile runs.

    Uses ``sentence-transformers/all-MiniLM-L6-v2`` (384 dimensions) with lazy
    import/model load. By default it only reads cached weights
    (``local_files_only=True``); set ``MEMEVAL_ALLOW_MODEL_DOWNLOAD=1`` to permit a
    first-run download. Importing or constructing this class never imports torch.
    """

    def __init__(
        self,
        model: str = _MINILM_MODEL,
        dim: int = _MINILM_DIM,
        *,
        cache_folder: Optional[str] = None,
        local_files_only: Optional[bool] = None,
        device: str = "cpu",
        batch_size: int = 32,
    ) -> None:
        self.model = model
        self.dim = dim
        self.cache_folder = cache_folder or os.environ.get(_MINILM_CACHE_ENV) or str(
            Path.home() / ".cache" / "cookbook-memory" / "sentence-transformers"
        )
        self.local_files_only = (
            os.environ.get(_MODEL_DOWNLOAD_ENV) != "1"
            if local_files_only is None
            else local_files_only
        )
        self.device = device
        self.batch_size = batch_size
        self._model: Any = None

    def __call__(self, text: str, *, input_type: Optional[str] = None) -> list:
        return self.embed(text, input_type=input_type)

    def embed(self, text: str, *, input_type: Optional[str] = None) -> list:
        """Embed one string and return a plain ``list[float]``."""
        return self.embed_batch([text], input_type=input_type)[0]

    def embed_batch(self, texts: list, *, input_type: Optional[str] = None) -> list:
        """Embed a batch with query/document encode methods when available."""
        if not texts:
            return []
        model = self._load_model()
        encode = self._encode_method(model, input_type)
        encoded = encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            precision="float32",
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        rows = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
        vectors = [self._coerce_vector(row) for row in rows]
        for vector in vectors:
            if len(vector) != self.dim:
                raise RuntimeError(
                    f"{self.model} returned {len(vector)} dimensions; expected {self.dim}"
                )
        return vectors

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise RuntimeError(
                "sentence-transformers is not installed; install the local ANN optional "
                "dependencies to use SentenceTransformersEmbedder."
            ) from exc
        try:
            self._model = SentenceTransformer(
                self.model,
                cache_folder=self.cache_folder,
                local_files_only=self.local_files_only,
                device=self.device,
            )
        except Exception as exc:
            hint = (
                f"{self.model} could not be loaded from cache_folder={self.cache_folder!r} "
                f"with local_files_only={self.local_files_only}. Set "
                f"{_MODEL_DOWNLOAD_ENV}=1 for the one-time model download."
            )
            raise RuntimeError(hint) from exc
        return self._model

    @staticmethod
    def _encode_method(model: Any, input_type: Optional[str]) -> Any:
        if input_type == "query" and hasattr(model, "encode_query"):
            return model.encode_query
        if input_type == "document" and hasattr(model, "encode_document"):
            return model.encode_document
        return model.encode

    @staticmethod
    def _coerce_vector(row: Any) -> list:
        values = row.tolist() if hasattr(row, "tolist") else list(row)
        return [float(x) for x in values]


def rebuild_store(items, dest_path: str, *, embed=None, embed_model: Optional[str] = None):
    """Re-embed ``items`` into a **fresh** ``SqliteVectorStore`` under ``embed``.

    Switching embedders changes the vector dimension, and a store mixes dimensions at
    its peril: ``_cosine`` raises on a query/stored mismatch. So you cannot retrofit a
    new embedder onto an existing DB — you rebuild. This helper writes each item into a
    new store at ``dest_path`` using the given ``embed`` (and optional ``embed_model``
    label) and returns it. ``SqliteVectorStore`` is imported lazily to keep this module
    dependency-light and import-cheap.
    """
    from .sqlite_store import SqliteVectorStore

    store = SqliteVectorStore(dest_path, embed=embed, embed_model=embed_model)
    for item in items:
        store.write(item)
    return store


__all__ = [
    "VoyageEmbedder",
    "SentenceTransformersEmbedder",
    "MockEmbedder",
    "rebuild_store",
]
