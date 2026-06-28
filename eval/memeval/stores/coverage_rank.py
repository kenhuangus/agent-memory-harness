"""Coverage re-ranking — a deterministic, no-LLM ranking signal over key overlap.

MRAgent ranks candidates by *how many* of the query's keys they satisfy (full vs partial
coverage). We don't have its key-graph, but the cheap, content-only adaptation is: take the
query's salient key tokens, over-fetch candidates from the inner retriever, and re-rank by a
blend of the retriever's similarity and the fraction of query keys present in each candidate
(`suggestion1.md` idea 3). Zero extra LLM cost; purely lexical, so it composes with any backend.

* :func:`key_tokens` — salient tokens of a string (lowercased alnum, length>=3, minus stopwords).
* :class:`CoverageRerankStore` — over-fetch ``fetch`` candidates, re-rank by
  ``alpha*similarity + (1-alpha)*coverage``, keep top-k.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..protocols import MemoryStore

# Small stoplist: English function words + a few code-generic terms that carry no selectivity.
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "while", "to", "of",
    "in", "on", "at", "by", "with", "from", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "it", "its", "do", "does", "did", "how", "what", "when",
    "where", "which", "who", "why", "can", "should", "would", "could", "will", "not", "no",
    "use", "used", "using", "make", "code", "file", "files", "function", "value", "values",
}
_TOK = re.compile(r"[a-z0-9]+")


def key_tokens(text: str) -> set[str]:
    return {t for t in _TOK.findall((text or "").lower()) if len(t) >= 3 and t not in _STOP}


class CoverageRerankStore:
    """Re-rank an inner store's top-N by ``alpha*similarity + (1-alpha)*key-coverage``."""

    def __init__(self, inner: MemoryStore, *, fetch: int = 30, alpha: float = 0.5) -> None:
        self._inner = inner
        self._fetch = fetch
        self._alpha = alpha

    def write(self, item) -> None:
        self._inner.write(item)

    def get(self, item_id: str):
        return self._inner.get(item_id)

    def all(self) -> list:
        return self._inner.all()

    def delete(self, item_id: str) -> bool:
        return self._inner.delete(item_id)

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs) -> list:
        if k <= 0:
            return []
        cands = self._inner.search(query, k=max(k, self._fetch), as_of=as_of, **kwargs)
        qk = key_tokens(query)
        if not qk or not cands:
            return cands[:k]
        # normalize similarity to [0,1] within this candidate set so the blend is scale-stable
        sims = [float(getattr(h, "score", 0.0)) for h in cands]
        lo, hi = min(sims), max(sims)
        span = (hi - lo) or 1.0
        scored = []
        for h, sim in zip(cands, sims):
            content = getattr(getattr(h, "item", None), "content", "") or ""
            cov = len(qk & key_tokens(content)) / len(qk)
            blended = self._alpha * ((sim - lo) / span) + (1.0 - self._alpha) * cov
            scored.append((blended, h))
        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [h for _, h in scored[:k]]
        for i, h in enumerate(ranked):
            try:
                h.rank = i
            except Exception:
                pass
        return ranked


__all__ = ["key_tokens", "CoverageRerankStore"]
