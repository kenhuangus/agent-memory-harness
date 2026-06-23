"""Graph-store backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

v1 is **stdlib-only and in-memory**: memories are nodes, OKF links are edges. As of Step 1 the edges are
**typed and directed**: each ``metadata["okf_links"]`` entry carrying a relation (e.g. ``(rel, target)``)
is classified (via :mod:`memeval.stores.relations`) into a closed-enum relation, and ``search`` resolves
the QUERY into a ``(relation, direction)`` intent and traverses only the matching edges — so "what does X
depend on" (out-edges) and "what depends on X" (in-edges) no longer return the same set. ("what breaks if
X changes" is an *impacts*-OUT query — what X impacts — not an in-edge query.)

**Where the typed links come from (Step 1b, DONE):** ``okf.py`` parsing now captures the markdown link
ANCHOR text alongside the target (``okf.py`` ``_LINK_RE``), emitting ``okf_links`` as ``(anchor, target)``
pairs, so **real OKF markdown links arrive typed** — a ``[depends on](x.md)`` link becomes a ``depends_on``
edge end-to-end. The graph store classifies the anchor (an untyped or empty anchor falls back to
``relates_to``, generic); ``okf.py`` stays a pure parser and does not import the relation vocabulary. The
OKF→GraphStore round-trip is covered by ``stores/tests/test_okf_to_graph.py``.

Edges live in two indexes maintained at ``write``: ``_out`` (source -> [(target, rel)]) and a reverse
``_in`` (target -> [(source, rel)]) — the reverse index makes IN/impact traversal O(deg) instead of the
old O(V*E) scan. Seeds are still scored by query-token overlap; a reached node scores
``seed_overlap * decay ** distance``.

**Back-compat.** An untyped OKF link (a bare target string, the pre-Step-1 format) is typed ``relates_to``
— a *generic* relationship that is traversed by ANY query in both directions. So an untyped corpus (the
D008 cascade fixture, ``test_graph_store``) behaves exactly as before: a "depends on" query finds the
untyped edges via the generic fallback, and a plain "related to X" query (no relation verb) traverses
every edge both ways. Typed filtering only narrows results when edges actually carry a type.

The paid-path upgrade keeps the contract: a typed-edge graph DB (Neo4j) behind the ``uri=`` seam.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Any, Optional

from ..schema import MemoryItem, RetrievedItem
from .relations import BOTH, IN, OUT, RELATES_TO, classify_relation, query_intent

_DECAY = 0.5     # score falloff per graph hop
_MAX_DEPTH = 2   # hops to traverse out from a seed
# Minimum query~node cosine for a SEMANTIC seed (only when an embedder is injected). PROVISIONAL — the
# captained real-embedder run tunes it; the offline default (embed=None) never reaches this path.
_SEMANTIC_SEED_FLOOR = 0.6


def _cosine(a, b) -> float:
    """Cosine similarity of two stdlib float vectors.

    Returns 0.0 if either is empty/None (a graph node may carry no embedding). Raises ``ValueError`` on a
    dimension mismatch between two NON-empty vectors — fail-loud on embedder/dim drift, matching
    ``SqliteVectorStore._cosine`` (a silent ``zip`` truncation could score mismatched vectors 1.0 and forge
    a semantic seed).
    """
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        raise ValueError(f"embedding dim mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


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

    v1 maps a link's basename-minus-.md to an item_id (so ``/memory/b.md`` -> ``b``), which holds when an
    item's id equals its OKF slug. Exact resolution via the target doc's ``x_item_id`` is a later refinement.
    """
    tail = str(link).rstrip("/").rsplit("/", 1)[-1]
    return tail[:-3] if tail.endswith(".md") else tail


def _entry_rel_target(entry: Any) -> tuple:
    """Normalize one ``okf_links`` entry to ``(relation, raw_target)``.

    Supports the typed forms — ``(anchor, target)`` / ``[anchor, target]`` / ``{"rel"/"relation", "target"}``
    (anchor text classified to a relation) — and the legacy untyped form, a bare target string (-> the
    generic ``relates_to``). Anything unexpected degrades to ``relates_to`` over its string form.
    """
    if isinstance(entry, str):
        return (RELATES_TO, entry)
    if isinstance(entry, dict):
        anchor = entry.get("rel") or entry.get("relation") or ""
        target = entry.get("target") or entry.get("to") or ""
        return (classify_relation(anchor), target)
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return (classify_relation(entry[0]), entry[1])
    return (RELATES_TO, str(entry))


class GraphStore:
    """In-memory typed/directed graph ``MemoryStore``: nodes + typed edge adjacency, seed-then-traverse."""

    def __init__(self, uri: Optional[str] = None, *, max_depth: int = _MAX_DEPTH,
                 embed: Optional[Any] = None, **kwargs: Any) -> None:
        self.uri = uri  # a real graph DB (Neo4j) is the paid-path seam; v1 is in-memory
        self.config = kwargs
        # BFS hops to traverse out from a seed. Default = _MAX_DEPTH (the speed end, byte-equivalent to the
        # pre-knob store); an accuracy profile raises it for deeper multi-hop reach (the speed<->accuracy
        # spectrum, D016). Clamped >= 0 (0 = seeds only, no traversal).
        self._max_depth = max(0, int(max_depth))
        # Optional text->vector embedder (the SAME embed= seam SqliteVectorStore / SemanticRouterClassifier
        # use). When set, search ALSO seeds nodes whose content is cosine-similar to the query (semantic
        # seeding, hybrid with lexical). None = lexical-only, byte-equivalent, offline zero-dependency.
        self._embed = embed
        self._embeddings: dict = {}   # item_id -> document embedding (populated at write when embed is set)
        self._nodes: dict = {}        # item_id -> MemoryItem
        self._order: list = []        # insertion order (for all())
        self._out: dict = {}          # item_id -> list of (target_id, relation)
        self._in: dict = {}           # item_id -> list of (source_id, relation)  (reverse index)

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        tokens = item.tokens
        if tokens <= 0 and item.content:
            tokens = _estimate_tokens(item.content)
        node = replace(item, tokens=tokens)  # always a copy -> never alias the caller's item
        edges = self._parse_edges(node)
        # Embed BEFORE mutating any index. A real injected embedder can raise (network/quota); a
        # half-applied write (node set but edges/embedding missing) would corrupt the graph. Compute the
        # doc vector first, mutate after -> write() stays all-or-nothing.
        doc_vec = self._doc_vector(node.content) if self._embed is not None else None

        # If rewriting a node, retract its old out-edges' reverse-index contributions first.
        if item.item_id in self._nodes:
            for tgt, rel in self._out.get(item.item_id, []):
                back = self._in.get(tgt)
                if back:
                    self._in[tgt] = [e for e in back if e != (item.item_id, rel)]
        else:
            self._order.append(item.item_id)

        self._nodes[item.item_id] = node
        if doc_vec is not None:
            self._embeddings[item.item_id] = doc_vec
        self._out[item.item_id] = edges
        for tgt, rel in edges:
            self._in.setdefault(tgt, []).append((item.item_id, rel))

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._nodes.get(item_id)

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
               **kwargs: Any) -> list:
        q = set(_tokenize(query))
        if not q:
            return []
        rel, direction = query_intent(query)
        # Per-call traversal-depth override (the cascade / accuracy profile injects this per query; the
        # default is the store's construction-time self._max_depth). Lets a profile traverse deeper for
        # one query without rebuilding the store. Clamped >= 0.
        _md = kwargs.get("max_depth")
        depth = self._max_depth if _md is None else max(0, int(_md))

        def visible(nid: str) -> bool:
            it = self._nodes.get(nid)
            return it is not None and not (as_of is not None and it.timestamp > as_of)

        # seeds: nodes whose content shares tokens with the query (lexical Jaccard) and/or — when an
        # embedder is injected — whose content is cosine-similar to the query (semantic seeding). HYBRID:
        # the seed score is max(lexical, semantic), so a lexical hit is never lost and a meaning-only node
        # the lexical path misses still enters the graph. embed=None -> lexical-only (byte-equivalent).
        qvec = self._query_vector(query) if self._embed is not None else None
        best: dict = {}
        frontier = deque()
        for nid in self._order:
            if not visible(nid):
                continue
            d = set(_tokenize(self._nodes[nid].content))
            union = len(q | d)
            score = len(q & d) / union if union else 0.0
            if qvec is not None:
                cos = _cosine(qvec, self._embeddings.get(nid))
                if cos >= _SEMANTIC_SEED_FLOOR:
                    score = max(score, cos)
            if score > 0:
                best[nid] = score
                frontier.append((nid, 0))
        if not best:
            return []

        # BFS out from the seeds along edges matching the query's (relation, direction) intent; a node's
        # score = max(seed_overlap * decay ** distance).
        while frontier:
            nid, dist = frontier.popleft()
            if dist >= depth:
                continue
            for nb in self._neighbors_for(nid, rel, direction):
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
    def _embed_call(self, text: str, input_type: str) -> list:
        """Embed ``text`` through the injected embedder, carrying the document/query asymmetry only when the
        embedder's signature accepts ``input_type`` (reusing SqliteVectorStore's seam helper)."""
        embed = self._embed
        if embed is None:                                       # never hit live (callers guard) — type-safe
            return []
        from .sqlite_store import _embedder_accepts_input_type  # lazy: avoid a module-load import cycle
        if _embedder_accepts_input_type(embed):
            return list(embed(text, input_type=input_type))
        return list(embed(text))

    def _doc_vector(self, text: str) -> list:
        return self._embed_call(text, "document")

    def _query_vector(self, text: str) -> list:
        return self._embed_call(text, "query")

    def _parse_edges(self, node: MemoryItem) -> list:
        """Typed out-edges ``[(target_id, relation)]`` from a node's ``okf_links`` (deduped, no self-loops)."""
        edges: list = []
        seen: set = set()
        for entry in (node.metadata or {}).get("okf_links", []) or []:
            rel, target_raw = _entry_rel_target(entry)
            tgt = _link_id(target_raw)
            if tgt and tgt != node.item_id and (tgt, rel) not in seen:
                seen.add((tgt, rel))
                edges.append((tgt, rel))
        return edges

    def _neighbors_for(self, nid: str, rel: str, direction: str) -> set:
        """Neighbors of ``nid`` reachable under the query intent ``(rel, direction)``.

        General intent (``relates_to``) traverses EVERY edge both ways (pre-typed behavior). A specific
        relation traverses that relation's edges in ``direction`` PLUS any ``relates_to`` (generic /
        untyped) edge both ways — so an untyped corpus is unaffected and typed edges add discrimination.
        """
        out_edges = self._out.get(nid, ())
        in_edges = self._in.get(nid, ())
        if rel == RELATES_TO:
            return {t for t, _ in out_edges} | {s for s, _ in in_edges}
        nbrs: set = set()
        if direction in (OUT, BOTH):
            nbrs |= {t for t, r in out_edges if r == rel}
        if direction in (IN, BOTH):
            nbrs |= {s for s, r in in_edges if r == rel}
        # generic relates_to edges are always traversable (both ways) — untyped back-compat.
        nbrs |= {t for t, r in out_edges if r == RELATES_TO}
        nbrs |= {s for s, r in in_edges if r == RELATES_TO}
        return nbrs


__all__ = ["GraphStore"]
