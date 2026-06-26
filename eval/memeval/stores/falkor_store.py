"""FalkorDB graph-store backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

PR1 is the Redis/openCypher sibling of :class:`memeval.stores.neo4j_store.Neo4jGraphStore`: an offline,
CI-landable parity FLOOR for the paid-path graph seam. It persists ``:Memory`` NODES plus their full
``metadata.okf_links`` edge source of truth, and emits ZERO relationship writes. Native ``[:REL]``
materialization/traversal is PR2 and deliberately absent here.

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

from ..schema import MemoryItem
from .graph_store import GraphStore, _MAX_DEPTH, _estimate_tokens

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


class FalkorGraphStore:
    """Graph ``MemoryStore`` over FalkorDB — the PR1 parity-floor port of ``GraphStore``.

    Construct with an injected ``client`` (real or fake) or a connection target (``host=`` or ``url=``).
    PR1 persists nodes only and delegates ``search`` to a transient in-memory ``GraphStore`` for exact
    id+order parity. NO module-load ``import falkordb``.
    """

    def __init__(self, host: Optional[str] = None, *, port: int = 6379, password: Optional[str] = None,
                 url: Optional[str] = None, client: Any = None, graph_name: str = "memory",
                 max_depth: int = _MAX_DEPTH, embed: Optional[Any] = None, **kwargs: Any) -> None:
        self.host = host
        self.port = port
        self._password = password
        self._url = url
        self.config = kwargs
        self._graph_name = graph_name
        self._max_depth = max(0, int(max_depth))
        self._embed = embed
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
        rows = self._read(_SEARCH_MATCH, {"as_of": as_of}).result_set
        items = [self._row_to_item(row[0].properties) for row in rows]
        transient = GraphStore(max_depth=self._max_depth, embed=self._embed)
        for item in items:
            transient.write(item)
        return transient.search(query, k=k, as_of=as_of, **kwargs)

    def delete(self, item_id: str) -> bool:
        if self._closed:
            raise RuntimeError("delete() on a closed FalkorGraphStore")
        res = self._write(_DELETE, {"id": item_id})
        return bool(getattr(res, "nodes_deleted", 0))


def _parse_edges(item: MemoryItem) -> list:
    """Validate ``okf_links`` with the in-memory parser; PR1 writes no relationships."""
    return GraphStore(max_depth=0)._parse_edges(item)


def _is_already_exists(exc: Exception) -> bool:
    text = str(exc).lower()
    return "already" in text and "index" in text


__all__ = ["FalkorGraphStore"]
