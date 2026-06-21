"""Markdown + YAML backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

OKF-native: persistence is delegated to :class:`memeval.okf.OKFStore`, so every
memory is an OKF concept document (markdown body + YAML frontmatter) on disk and a
run's memory is a portable, spec-conformant bundle. On top of that this class adds
the **inverted keyword index** the architecture specs for fast literal recall
(``token -> {item_id}``).

``search`` returns ONLY items that share at least one token with the query — a
keyword index answers "what do I literally know that overlaps this query?", so
zero-overlap items are never padded in. Matches are ranked by the SAME Okapi BM25
scorer and tie-breaks the reference store uses (the shared
:func:`memeval.harness._bm25_scores`, so cross-backend comparisons stay fair and
the two stores can never drift apart), with ``rank``/``score``/``tokens`` set and
``as_of`` honored. ``score`` is the raw BM25 value (non-negative, unbounded -- NOT
``[0, 1]``). An empty query has no tokens and returns ``[]``.

Stdlib-only: the tokenizer mirrors ``harness._tokenize`` (kept local so this backend
carries its own dependencies); a parity test guards against drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..harness import _bm25_scores
from ..okf import OKFStore
from ..schema import MemoryItem, RetrievedItem


def _tokenize(text: str) -> list[str]:
    """Lowercase alnum-run tokens. Stdlib-only, deterministic.

    Mirrors the reference ranking's tokenizer so the markdown backend ranks
    matches identically to ``InMemoryStore`` (see the parity test).
    """
    out: list[str] = []
    cur: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


class MarkdownStore:
    """OKF-native ``MemoryStore`` with an inverted keyword index for literal recall."""

    def __init__(self, path: str | Path, *, autoload: bool = True) -> None:
        self._okf = OKFStore(path, autoload=autoload)
        self._postings: dict[str, set[str]] = {}     # token -> {item_id}
        self._item_tokens: dict[str, set[str]] = {}   # item_id -> its tokens (clean overwrite)
        for item in self._okf.all():                  # index whatever autoloaded from disk
            self._index(item)

    # -- inverted-index maintenance ----------------------------------------
    def _index(self, item: MemoryItem) -> None:
        """(Re)index ``item``, dropping any stale postings from a prior version."""
        self._deindex(item.item_id)
        tokens = set(_tokenize(item.content))
        self._item_tokens[item.item_id] = tokens
        for token in tokens:
            self._postings.setdefault(token, set()).add(item.item_id)

    def _deindex(self, item_id: str) -> None:
        for token in self._item_tokens.pop(item_id, set()):
            ids = self._postings.get(token)
            if ids is not None:
                ids.discard(item_id)
                if not ids:
                    del self._postings[token]

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        """Persist ``item`` as an OKF doc (idempotent on id) and (re)index it."""
        self._okf.write(item)  # writes the bundle doc and populates item.tokens
        self._index(item)

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._okf.get(item_id)

    def search(
        self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any
    ) -> list[RetrievedItem]:
        """Top-``k`` genuine keyword matches, best-ranked first.

        Candidates come from the inverted index (items sharing >=1 query token);
        each is scored by the SAME shared Okapi BM25 scorer the reference store
        uses (:func:`memeval.harness._bm25_scores`) and ordered by BM25 desc, then
        IDF-weighted query coverage, then write-time ``relevancy``, ``timestamp``,
        ``item_id`` — so the two backends rank identical candidate sets identically
        (the parity test). ``score`` is the raw BM25 value (non-negative,
        unbounded). ``as_of`` drops items newer than the query. An empty query
        returns ``[]`` (the keyword-index contract: no tokens, no candidates).
        """
        q = set(_tokenize(query))
        if not q:
            return []

        candidate_ids: set[str] = set()
        for token in q:
            candidate_ids |= self._postings.get(token, set())

        candidates: list[MemoryItem] = []
        for item_id in candidate_ids:
            item = self._okf.get(item_id)
            if item is None:
                continue
            if as_of is not None and item.timestamp > as_of:
                continue  # no peeking at the future
            candidates.append(item)

        bm25 = _bm25_scores(query, [(it.item_id, it.content) for it in candidates])

        def sort_key(it: MemoryItem) -> tuple[float, float, float, float, str]:
            score, cover = bm25[it.item_id]
            return (-score, -cover, -it.relevancy, -it.timestamp, it.item_id)

        candidates.sort(key=sort_key)
        return [
            RetrievedItem(item=item, score=bm25[item.item_id][0], rank=rank)
            for rank, item in enumerate(candidates[: max(0, k)])
        ]

    def all(self) -> list[MemoryItem]:
        return self._okf.all()


__all__ = ["MarkdownStore"]
