"""FalkorDB graph-store backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

PR1 is the Redis/openCypher sibling of :class:`memeval.stores.neo4j_store.Neo4jGraphStore`: an offline,
CI-landable parity FLOOR for the paid-path graph seam. It persists ``:Memory`` NODES plus their full
``metadata.okf_links`` edge source of truth. PR2 keeps that floor as the default and adds opt-in
``native=True`` materialization/traversal over typed ``[:REL]`` relationships.

Parity is achieved by construction. ``search`` fetches the as_of-visible nodes from FalkorDB, reconstructs
``MemoryItem`` rows, builds a transient in-memory :class:`GraphStore`, and delegates scoring/BFS/tie-break to
that store. Reusing the in-memory scorer is what guarantees byte-identical id sets and order.

The real ``falkordb`` package is lazy and fail-loud: importing this module never imports ``falkordb`` or
``redis``. A set host/url with no importable package raises ``RuntimeError``; no target and no injected
client also raises. The offline default remains the stdlib in-memory ``GraphStore``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Optional

from ..schema import MemoryItem, RetrievedItem
from .graph_store import GraphStore, _MAX_DEPTH, _estimate_tokens, _tokenize
from .relations import (
    BOTH,
    CALLS,
    CONFLICTS_WITH,
    CONTRADICTS,
    DEPENDS_ON,
    IMPACTS,
    IMPORTS,
    IN,
    OUT,
    RELATES_TO,
    RENAMES,
    USES,
    query_intent,
)

_NODE_LABEL = "Memory"
_NODE_MERGE = (
    f"MERGE (n:{_NODE_LABEL} {{item_id: $item_id}}) "
    f"ON CREATE SET n.seq = $seq "
    f"SET n += $props"
)
_INDEX_CREATE = f"CREATE INDEX FOR (n:{_NODE_LABEL}) ON (n.item_id)"
_MAX_SEQ = f"MATCH (n:{_NODE_LABEL}) RETURN coalesce(max(n.seq), -1) AS m"
_SEARCH_MATCH = (
    f"MATCH (n:{_NODE_LABEL}) WHERE $as_of IS NULL OR coalesce(n.timestamp, 0.0) <= $as_of RETURN n"
)
_GET_MATCH = f"MATCH (n:{_NODE_LABEL} {{item_id: $id}}) RETURN n"
_ALL_MATCH = f"MATCH (n:{_NODE_LABEL}) RETURN n ORDER BY n.seq"
_DELETE = f"MATCH (n:{_NODE_LABEL} {{item_id: $id}}) DETACH DELETE n"
_REL_RESET = f"MATCH (:{_NODE_LABEL})-[r:REL]->(:{_NODE_LABEL}) DELETE r"
_REL_MERGE = (
    "UNWIND $edges AS e "
    f"MATCH (a:{_NODE_LABEL} {{item_id: e.src}}) "
    f"MATCH (b:{_NODE_LABEL} {{item_id: e.tgt}}) "
    "MERGE (a)-[r:REL {rel_type: e.rel}]->(b)"
)
_DEFAULT_REL_WEIGHTS = {
    DEPENDS_ON: 0.7,
    CALLS: 0.7,
    IMPORTS: 0.6,
    USES: 0.6,
    IMPACTS: 0.7,
    RENAMES: 0.6,
    CONFLICTS_WITH: 0.5,
    CONTRADICTS: 0.5,
    RELATES_TO: 0.5,
}
_TRAVERSE = {
    OUT: f"MATCH p=(s:{_NODE_LABEL})-[:REL*1..{{d}}]->(m:{_NODE_LABEL}) ",
    IN: f"MATCH p=(s:{_NODE_LABEL})<-[:REL*1..{{d}}]-(m:{_NODE_LABEL}) ",
    BOTH: f"MATCH p=(s:{_NODE_LABEL})-[:REL*1..{{d}}]-(m:{_NODE_LABEL}) ",
}
_REL_FILTER = (
    "WHERE s.item_id IN $seed_ids "
    "AND ($rel = 'relates_to' OR all(e IN relationships(p) "
    "WHERE e.rel_type = $rel OR e.rel_type = 'relates_to')) "
    "AND ($as_of IS NULL OR all(n IN nodes(p) WHERE coalesce(n.timestamp, 0.0) <= $as_of)) "
)
_WEIGHT_CASE = (
    "CASE e.rel_type "
    "WHEN 'depends_on' THEN $w_depends_on "
    "WHEN 'calls' THEN $w_calls "
    "WHEN 'imports' THEN $w_imports "
    "WHEN 'uses' THEN $w_uses "
    "WHEN 'impacts' THEN $w_impacts "
    "WHEN 'renames' THEN $w_renames "
    "WHEN 'conflicts_with' THEN $w_conflicts_with "
    "WHEN 'contradicts' THEN $w_contradicts "
    "WHEN 'relates_to' THEN $w_relates_to "
    "ELSE $w_default END"
)
_TRAVERSE_RETURN = (
    "WITH m, s.item_id AS seed_id, "
    f"reduce(prod = 1.0, e IN relationships(p) | prod * {_WEIGHT_CASE}) AS path_weight "
    "RETURN m.item_id AS item_id, seed_id, sum(path_weight) AS path_weight"
)


class FalkorGraphStore:
    """Graph ``MemoryStore`` over FalkorDB — the PR1 parity-floor port of ``GraphStore``.

    Construct with an injected ``client`` (real or fake) or a connection target (``host=`` or ``url=``).
    PR1 persists nodes only and delegates ``search`` to a transient in-memory ``GraphStore`` for exact
    id+order parity. NO module-load ``import falkordb``.
    """

    def __init__(self, host: Optional[str] = None, *, port: int = 6379, password: Optional[str] = None,
                 url: Optional[str] = None, client: Any = None, graph_name: str = "memory",
                 max_depth: int = _MAX_DEPTH, embed: Optional[Any] = None, native: bool = False,
                 rel_weights: Optional[dict] = None, **kwargs: Any) -> None:
        self.host = host
        self.port = port
        self._password = password
        self._url = url
        self.config = kwargs
        self._graph_name = graph_name
        self._max_depth = max(0, int(max_depth))
        self._embed = embed
        self._native = bool(native)
        self._dirty = True
        self._rel_weights = dict(_DEFAULT_REL_WEIGHTS)
        if rel_weights is not None:
            self._rel_weights.update(rel_weights)
        self._closed = False
        self._seq = 0

        if client is not None:
            self._db = client
            self._owns_client = False
        elif host is not None or url is not None:
            self._db = self.connect()
            self._owns_client = True
        else:
            raise RuntimeError(
                "FalkorGraphStore needs host=/url= or an injected client — it is not an offline default "
                "(the offline default is the in-memory GraphStore)."
            )

        self._graph = self._db.select_graph(self._graph_name)
        try:
            self._ensure_index()
            self._seq = self._read_max_seq() + 1
        except Exception:
            if self._owns_client:
                self.close()
            raise

    # -- client / lifecycle ------------------------------------------------
    def connect(self) -> Any:
        """Lazily import ``falkordb`` and build a real client. Fail loud if it is unavailable."""
        try:
            import falkordb  # lazy: the ONLY import of falkordb in this module
        except Exception as exc:
            raise RuntimeError(
                f"FalkorGraphStore(host={self.host!r}, url={self._url!r}) requires the 'falkordb' package, "
                "which is not importable (no offline fallback — use the in-memory GraphStore for offline "
                "runs, or inject a client)."
            ) from exc
        if self._url is not None:
            return falkordb.FalkorDB.from_url(self._url)
        return falkordb.FalkorDB(host=self.host, port=self.port, password=self._password)

    def _ensure_index(self) -> None:
        try:
            self._write(_INDEX_CREATE)
        except Exception as exc:
            if not _is_already_exists(exc):
                raise

    def _read_max_seq(self) -> int:
        res = self._read(_MAX_SEQ)
        m = res.result_set[0][0] if getattr(res, "result_set", None) else -1
        return int(m) if m is not None else -1

    def _read(self, cypher: str, params: Optional[dict] = None) -> Any:
        if self._closed or self._db is None or self._graph is None:
            raise RuntimeError("operation on a closed FalkorGraphStore")
        return self._graph.ro_query(cypher, params=params)

    def _write(self, cypher: str, params: Optional[dict] = None) -> Any:
        if self._closed or self._db is None or self._graph is None:
            raise RuntimeError("operation on a closed FalkorGraphStore")
        return self._graph.query(cypher, params=params)

    def close(self) -> None:
        client = getattr(self, "_db", None)
        if client is not None and hasattr(client, "close"):
            client.close()
        self._db = None
        self._graph = None
        self._closed = True

    def __enter__(self) -> "FalkorGraphStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        """Upsert one ``:Memory`` node. PR1 emits no native relationship writes."""
        if self._closed:
            raise RuntimeError("write() on a closed FalkorGraphStore")
        tokens = item.tokens
        if tokens <= 0 and item.content:
            tokens = _estimate_tokens(item.content)
        node = replace(item, tokens=tokens) if tokens != item.tokens else item
        _ = _parse_edges(node)  # validation only; no rel write
        props = self._props(node)
        seq = self._seq
        self._write(_NODE_MERGE, {"item_id": node.item_id, "props": props, "seq": seq})
        self._seq += 1
        if self._native:
            self._dirty = True

    def _props(self, item: MemoryItem) -> dict:
        props = {
            "item_id": item.item_id,
            "content": item.content,
            "timestamp": item.timestamp,
            "relevancy": item.relevancy,
            "tags": json.dumps(list(item.tags)),
            "tokens": item.tokens,
            "version": item.version,
            "metadata": json.dumps(item.metadata or {}),
        }
        if item.session_id is not None:
            props["session_id"] = item.session_id
        if item.source is not None:
            props["source"] = item.source
        return props

    def _row_to_item(self, props: dict) -> MemoryItem:
        rel = props.get("relevancy")
        ts = props.get("timestamp")
        ver = props.get("version")
        toks = props.get("tokens")
        raw_tags = props.get("tags")
        raw_meta = props.get("metadata")
        tags = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags or [])
        metadata = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta or {})
        return MemoryItem(
            item_id=props["item_id"],
            content=props.get("content") or "",
            timestamp=float(ts) if ts is not None else 0.0,
            relevancy=float(rel) if rel is not None else 1.0,
            session_id=props.get("session_id"),
            source=props.get("source"),
            tags=tags,
            tokens=int(toks) if toks is not None else 0,
            version=int(ver) if ver is not None else 1,
            metadata=metadata,
        )

    def get(self, item_id: str) -> Optional[MemoryItem]:
        rows = self._read(_GET_MATCH, {"id": item_id}).result_set
        return self._row_to_item(rows[0][0].properties) if rows else None

    def all(self) -> list:
        rows = self._read(_ALL_MATCH).result_set
        return [self._row_to_item(row[0].properties) for row in rows]

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any) -> list:
        if not self._native:
            return self._search_floor(query, k=k, as_of=as_of, **kwargs)
        return self._search_native(query, k=k, as_of=as_of, **kwargs)

    def _search_floor(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any) -> list:
        rows = self._read(_SEARCH_MATCH, {"as_of": as_of}).result_set
        items = [self._row_to_item(row[0].properties) for row in rows]
        transient = GraphStore(max_depth=self._max_depth, embed=self._embed)
        for item in items:
            transient.write(item)
        return transient.search(query, k=k, as_of=as_of, **kwargs)

    def materialize(self) -> None:
        if self._closed:
            raise RuntimeError("materialize() on a closed FalkorGraphStore")
        self._write(_REL_RESET)
        edges = []
        for item in self.all():
            for tgt, rel in _parse_edges(item):
                edges.append({"src": item.item_id, "tgt": tgt, "rel": rel})
        if edges:
            self._write(_REL_MERGE, {"edges": edges})
        self._dirty = False

    def _search_native(self, query: str, *, k: int = 5, as_of: Optional[float] = None,
                       **kwargs: Any) -> list:
        q = set(_tokenize(query))
        if not q:
            return []
        items = [item for item in self.all()
                 if not (as_of is not None and item.timestamp > as_of)]
        seed_scores = self._seed_scores(q, items)
        if not seed_scores:
            return []

        depth_arg = kwargs.get("max_depth")
        depth = self._max_depth if depth_arg is None else max(0, int(depth_arg))
        if depth <= 0:
            return self._rank(seed_scores, {item.item_id: item for item in items}, k=k)

        if self._dirty:
            self.materialize()

        rel, direction = query_intent(query)
        cypher = _TRAVERSE[direction].format(d=int(depth)) + _REL_FILTER + _TRAVERSE_RETURN
        params = {
            "seed_ids": list(seed_scores),
            "rel": rel,
            "as_of": as_of,
            **_weight_params(self._rel_weights),
        }
        rows = self._read(cypher, params).result_set
        scores: dict[str, float] = {}
        for row in rows:
            item_id, seed_id, path_weight = row[0], row[1], row[2]
            seed_score = seed_scores.get(seed_id)
            if seed_score is None:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + seed_score * float(path_weight or 0.0)
        return self._rank(scores, {item.item_id: item for item in items}, k=k)

    def _seed_scores(self, q: set, items: list[MemoryItem]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for item in items:
            d = set(_tokenize(item.content))
            union = len(q | d)
            score = len(q & d) / union if union else 0.0
            if score > 0:
                scores[item.item_id] = score
        return scores

    def _rank(self, scores: dict[str, float], item_by_id: dict[str, MemoryItem], *, k: int) -> list:
        scored = [(score, item_by_id[item_id]) for item_id, score in scores.items()
                  if score > 0 and item_id in item_by_id]
        scored.sort(key=lambda si: (-si[0], -si[1].relevancy, -si[1].timestamp, si[1].item_id))
        return [RetrievedItem(item=it, score=sc, rank=r)
                for r, (sc, it) in enumerate(scored[: max(0, k)])]

    def delete(self, item_id: str) -> bool:
        if self._closed:
            raise RuntimeError("delete() on a closed FalkorGraphStore")
        res = self._write(_DELETE, {"id": item_id})
        deleted = bool(getattr(res, "nodes_deleted", 0))
        if deleted and self._native:
            self._dirty = True
        return deleted


def _parse_edges(item: MemoryItem) -> list:
    """Validate ``okf_links`` with the in-memory parser; PR1 writes no relationships."""
    return GraphStore(max_depth=0)._parse_edges(item)


def _is_already_exists(exc: Exception) -> bool:
    text = str(exc).lower()
    return "already" in text and ("index" in text or "indexed" in text)


def _weight_params(weights: dict) -> dict:
    params = {f"w_{rel}": float(weights.get(rel, _DEFAULT_REL_WEIGHTS[rel]))
              for rel in _DEFAULT_REL_WEIGHTS}
    params["w_default"] = float(weights.get(RELATES_TO, _DEFAULT_REL_WEIGHTS[RELATES_TO]))
    return params


__all__ = ["FalkorGraphStore"]
