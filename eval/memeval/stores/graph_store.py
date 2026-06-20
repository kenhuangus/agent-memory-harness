"""Graph-store backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

v1 is **stdlib-only and in-memory**: memories are nodes, and OKF links
(``metadata["okf_links"]``) are directed edges. ``search`` scores **seed** nodes by
token overlap with the query, then traverses the link neighborhood (BFS, bounded
depth), scoring each reached node as ``seed_overlap * decay ** distance``. So a
query that matches one node also surfaces its *linked* neighbors — the relationship
retrieval the router sends here, that the keyword/vector backends can't do.

The paid-path upgrade keeps the contract: a typed-edge graph DB (Neo4j) behind the
``uri=`` seam. v1 edges are **untyped** (OKF links carry no type) and traversal is
**undirected** (a relationship is a relationship in either direction); typed/
directional traversal is a later refinement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Any, Optional

from ..schema import MemoryItem, RetrievedItem

_DECAY = 0.5     # score falloff per graph hop
_MAX_DEPTH = 2   # hops to traverse out from a seed


def _tokenize(text: str) -> list:
    """Lowercase alnum-run tokens (stdlib; matches the other backends' tokenizer)."""
    out: list = []
    cur: list = []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _estimate_tokens(content: str) -> int:
    try:
        from ..models import estimate_tokens  # shared estimate, for cross-store consistency
        return estimate_tokens(content)
    except Exception:
        return max(1, len(content or "") // 4)


def _link_id(link: Any) -> str:
    """Normalize an OKF link target to a candidate item_id (last path segment, no .md).

    v1 maps a link's basename-minus-.md to an item_id (so ``/memory/b.md`` -> ``b``),
    which holds when an item's id equals its OKF slug. Exact resolution via the target
    doc's ``x_item_id`` is a later refinement.
    """
    tail = str(link).rstrip("/").rsplit("/", 1)[-1]
    return tail[:-3] if tail.endswith(".md") else tail


class GraphStore:
    """In-memory graph ``MemoryStore``: nodes + link adjacency, seed-then-traverse search."""

    def __init__(self, uri: Optional[str] = None, **kwargs: Any) -> None:
        self.uri = uri  # a real graph DB (Neo4j) is the paid-path seam; v1 is in-memory
        self.config = kwargs
        self._nodes: dict = {}        # item_id -> MemoryItem
        self._order: list = []        # insertion order (for all())
        self._out: dict = {}          # item_id -> set of out-edge target ids (from links)

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        tokens = item.tokens
        if tokens <= 0 and item.content:
            tokens = _estimate_tokens(item.content)
        node = replace(item, tokens=tokens)  # always a copy -> never alias the caller's item
        if item.item_id not in self._nodes:
            self._order.append(item.item_id)
        self._nodes[item.item_id] = node
        self._out[item.item_id] = {
            t for t in (_link_id(ln) for ln in (node.metadata or {}).get("okf_links", []))
            if t and t != item.item_id
        }

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._nodes.get(item_id)

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list:
        q = set(_tokenize(query))
        if not q:
            return []

        def visible(nid: str) -> bool:
            it = self._nodes.get(nid)
            return it is not None and not (as_of is not None and it.timestamp > as_of)

        # seeds: nodes whose content shares tokens with the query (Jaccard overlap)
        best: dict = {}
        frontier = deque()
        for nid in self._order:
            if not visible(nid):
                continue
            d = set(_tokenize(self._nodes[nid].content))
            union = len(q | d)
            overlap = len(q & d) / union if union else 0.0
            if overlap > 0:
                best[nid] = overlap
                frontier.append((nid, 0))
        if not best:
            return []

        # BFS out from the seeds; a node's score = max(seed_overlap * decay ** distance)
        while frontier:
            nid, dist = frontier.popleft()
            if dist >= _MAX_DEPTH:
                continue
            for nb in self._neighbors(nid):
                if not visible(nb):
                    continue
                cand = best[nid] * _DECAY
                if cand > best.get(nb, 0.0):
                    best[nb] = cand
                    frontier.append((nb, dist + 1))

        scored = [(sc, self._nodes[nid]) for nid, sc in best.items()]
        scored.sort(key=lambda si: (-si[0], -si[1].relevancy, -si[1].timestamp, si[1].item_id))
        return [RetrievedItem(item=it, score=sc, rank=r)
                for r, (sc, it) in enumerate(scored[: max(0, k)])]

    def all(self) -> list:
        return [self._nodes[i] for i in self._order]

    # -- helpers -----------------------------------------------------------
    def _neighbors(self, nid: str) -> set:
        """Undirected neighbors: out-edges plus in-edges (a relationship runs both ways).

        TODO: the in-edge scan is O(V*E) per search; a maintained reverse-adjacency
        index would make it O(deg). Deferred — the paid path uses a real graph DB.
        """
        nbrs = set(self._out.get(nid, ()))
        for src, outs in self._out.items():
            if nid in outs:
                nbrs.add(src)
        return nbrs


__all__ = ["GraphStore"]
