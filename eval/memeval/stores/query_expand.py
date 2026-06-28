"""Query expansion for retrieval — multi-query recall with LLM-generated alternatives.

A single query phrasing under-recalls: proper nouns, plurals, and tense/form variants that a
real match uses but the query doesn't are missed by both vector and lexical retrieval. The fix
(MRAgent's mandatory-alternatives idea, `suggestion1.md` idea 2): expand the query into a few
alternative phrasings (synonyms, different tense/form, related terms), retrieve for each, and
merge the candidates by their best score.

* :class:`LLMQueryExpander` — asks a capable model for up to N alternative phrasings (JSON list).
  Paid at call time, not import time. A failure returns no alternatives (degrades to single-query).
* :class:`MockQueryExpander` — deterministic offline expander (stdlib morphology: plural/tense
  suffix toggles) for tests/CI; demonstrates the MECHANISM, not the LLM's semantic lift.
* :class:`ExpandedQueryStore` — a :class:`~memeval.protocols.MemoryStore` facade that runs the
  original query plus each variant through ``inner`` and merges hits by max score. Composes with
  any backend (and with :class:`~memeval.stores.rerankers.RerankedStore`).

Like the reranker, the offline lift is mechanism-only; the real lift is a captained LLM run.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..protocols import MemoryStore

_EXPAND_SYSTEM = "You expand a code-task search query into alternative phrasings for retrieval."
_EXPAND_PROMPT = (
    "Query:\n{q}\n\nProduce up to {n} ALTERNATIVE phrasings that a relevant memory might use: "
    "synonyms, different tense/form (singular/plural, past/present), and closely-related terms. "
    "Keep each short. Do NOT answer the query. Return ONLY a JSON list of strings."
)


class LLMQueryExpander:
    """Generate up to ``max_variants`` alternative query phrasings via an LLM client."""

    def __init__(self, client: Any, *, max_variants: int = 4) -> None:
        self._client = client
        self._n = max_variants

    def __call__(self, query: str) -> list[str]:
        try:
            out = self._client.complete(
                _EXPAND_PROMPT.format(q=query[:1500], n=self._n),
                system=_EXPAND_SYSTEM, max_tokens=200,
            )
            text = (out.text or "").strip()
            m = re.search(r"\[.*\]", text, re.S)
            arr = json.loads(m.group(0) if m else text)
            return [str(s).strip() for s in arr if str(s).strip()][: self._n]
        except Exception:
            return []  # degrade to single-query retrieval


class MockQueryExpander:
    """Offline morphological expander (mechanism only — no semantic lift)."""

    def __init__(self, *, max_variants: int = 4) -> None:
        self._n = max_variants

    def __call__(self, query: str) -> list[str]:
        toks = query.split()
        out: list[str] = []
        # toggle a trailing 's' on the last token; toggle a trailing 'ing'/'ed' — cheap form variants
        if toks:
            last = toks[-1]
            alt = last[:-1] if last.endswith("s") else last + "s"
            out.append(" ".join(toks[:-1] + [alt]))
            if last.endswith("ing"):
                out.append(" ".join(toks[:-1] + [last[:-3]]))
            elif last.endswith("ed"):
                out.append(" ".join(toks[:-1] + [last[:-2]]))
        seen, uniq = {query}, []
        for v in out:
            if v not in seen:
                seen.add(v); uniq.append(v)
        return uniq[: self._n]


class ExpandedQueryStore:
    """Facade: retrieve for the query + each expansion, merge candidates by max score."""

    def __init__(self, inner: MemoryStore, expander: Any, *, fetch_per_query: int = 10) -> None:
        self._inner = inner
        self._expand = expander
        self._fetch = fetch_per_query

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
        queries = [query] + list(self._expand(query))
        fetch = max(k, self._fetch)
        best: dict[str, Any] = {}
        for q in queries:
            for h in self._inner.search(q, k=fetch, as_of=as_of, **kwargs):
                cur = best.get(h.item_id)
                if cur is None or getattr(h, "score", 0.0) > getattr(cur, "score", 0.0):
                    best[h.item_id] = h
        ranked = sorted(best.values(), key=lambda h: getattr(h, "score", 0.0), reverse=True)[:k]
        for i, h in enumerate(ranked):
            try:
                h.rank = i
            except Exception:
                pass
        return ranked


__all__ = ["LLMQueryExpander", "MockQueryExpander", "ExpandedQueryStore"]
