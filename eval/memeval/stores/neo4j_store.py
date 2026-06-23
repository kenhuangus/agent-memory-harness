"""Neo4j graph-store backend — owner: Brent (@bgibson1618). Implements ``MemoryStore``.

This is the **paid-path upgrade behind the graph store's ``uri=`` seam**: a real typed-edge graph DB
(Neo4j, over the Bolt driver) standing in for the in-memory :class:`memeval.stores.graph_store.GraphStore`.

**Phase A — a parity FLOOR (this module).** Neo4j must reproduce the in-memory ``GraphStore``'s retrieval
**id-set AND order EXACTLY**. It is a no-op on accuracy *for now*; Phase B later adds Neo4j-native accuracy
(Cypher/GDS traversal + scoring) and this parity floor becomes its regression guard. Parity is achieved BY
CONSTRUCTION — **the golden rule**: ``search`` does NOT reimplement seeding / BFS / scoring / tie-break.
It pulls the as_of-visible nodes out of Neo4j, reconstructs the :class:`MemoryItem` rows, builds a
**transient in-memory ``GraphStore``** from them, and delegates to ``transient.search(...)``. Reuse of the
in-memory scorer is what guarantees identical ids+order; reimplementing the algorithm in Cypher would
reopen the float/order divergence the scope doc warns about (that is explicitly Phase B's job, not this).

**The Neo4j graph is genuinely typed and real.** ``write`` MERGEs a ``:Memory`` node keyed on ``item_id``
and MERGEs a typed ``[:REL {rel_type}]`` relationship per ``okf_links`` edge — so Neo4j carries the real
typed graph (the Phase-B substrate). Phase-A READS rebuild edges from each node's ``okf_links`` (the single
source of truth, exactly how the ``path=`` SQLite seam rebuilds them on load), so the typed relationships
are written-but-not-yet-traversed: a faithful port first, native traversal later.

**Lazy + fail-loud (mirrors :class:`VoyageEmbedder`).** ``neo4j`` is NOT imported at module load — it is
imported lazily ONLY inside :meth:`connect`. A set ``uri`` with no importable ``neo4j`` raises a clear
:class:`RuntimeError` (never a silent offline fallback — that would mislabel an offline run as a paid graph
run). No ``uri`` and no injected ``driver`` also raises: a graph-DB backend needs one of them. The offline
default stays the in-memory ``GraphStore`` (this module never touches the offline path).
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Optional

from ..schema import MemoryItem
from .graph_store import GraphStore, _MAX_DEPTH, _estimate_tokens

# The graph node label + the relationship type. ``REL`` carries a ``rel_type`` property (the classified
# closed-enum relation) so a single relationship type spans the whole typed vocabulary — Phase B keys
# traversal off ``rel_type``.
_NODE_LABEL = "Memory"
# seq is CREATE-ONLY (`ON CREATE SET n.seq`): a brand-new node stamps its insertion seq; a rewrite of an
# existing node MERGEs its props but PRESERVES the original seq, so an in-place update never bumps the node
# to the tail of all() (the seq-in-$props form did, reordering all() on rewrite). The plain `SET n += $props`
# carries the mutable fields; seq rides as its OWN parameter so it can be ON CREATE-scoped.
_NODE_MERGE = (
    f"MERGE (n:{_NODE_LABEL} {{item_id: $item_id}}) "
    f"ON CREATE SET n.seq = $seq "
    f"SET n += $props"
)
_REL_MERGE = (
    f"MERGE (a:{_NODE_LABEL} {{item_id: $src}}) "
    f"MERGE (b:{_NODE_LABEL} {{item_id: $tgt}}) "
    f"MERGE (a)-[r:REL {{rel_type: $rel}}]->(b)"
)
_CONSTRAINT = (
    f"CREATE CONSTRAINT memory_item_id IF NOT EXISTS "
    f"FOR (n:{_NODE_LABEL}) REQUIRE n.item_id IS UNIQUE"
)
# Highest seq currently in the DB (-1 when empty) -> the constructor seeds self._seq = m + 1, so a 2nd
# instance / process restart over the SAME DB CONTINUES the sequence instead of restarting at 0 (which would
# collide existing seqs and scramble all() order). Read once after the constraint is ensured.
_MAX_SEQ = f"MATCH (n:{_NODE_LABEL}) RETURN coalesce(max(n.seq), -1) AS m"
# as_of is pushed into Cypher for a real no-leak bound; the transient search honors it again (identical
# result either way — belt and suspenders, and the parameter on the wire is what the parity eval asserts).
# coalesce(n.timestamp, 0.0): a node with a NULL timestamp (an externally-written / Phase-B node — the store
# itself always writes a float) is treated as ts 0.0, matching _row_to_item (ts -> 0.0) AND the in-memory
# baseline, which treats a 0.0 timestamp as visible under any as_of >= 0.
_SEARCH_MATCH = (
    f"MATCH (n:{_NODE_LABEL}) WHERE $as_of IS NULL OR coalesce(n.timestamp, 0.0) <= $as_of RETURN n"
)
_GET_MATCH = f"MATCH (n:{_NODE_LABEL} {{item_id: $id}}) RETURN n"
_ALL_MATCH = f"MATCH (n:{_NODE_LABEL}) RETURN n ORDER BY n.seq"
_DELETE = f"MATCH (n:{_NODE_LABEL} {{item_id: $id}}) DETACH DELETE n RETURN count(n) AS c"


class Neo4jGraphStore:
    """Typed graph ``MemoryStore`` over the Neo4j Bolt driver — a parity-floor port of ``GraphStore``.

    Construct with an injected ``driver`` (a real ``neo4j`` driver OR a test fake) or a ``uri`` (lazily
    builds a real driver via :meth:`connect`). ``search`` delegates scoring/BFS/tie-break to a transient
    in-memory ``GraphStore`` for exact id+order parity. NO module-load ``import neo4j``.
    """

    def __init__(self, uri: Optional[str] = None, *, auth: Any = None, driver: Any = None,
                 database: str = "neo4j", max_depth: int = _MAX_DEPTH,
                 embed: Optional[Any] = None, **kwargs: Any) -> None:
        self.uri = uri
        self._auth_value = auth  # stored so connect() passes real-driver auth (a set auth was silently dropped)
        self.config = kwargs
        self._database = database
        self._max_depth = max(0, int(max_depth))
        self._embed = embed
        self._closed = False
        self._seq = 0  # monotonic insertion counter -> all() ordering (mirrors GraphStore._order)

        # Driver resolution: injected driver wins (real or fake); else a uri lazily builds one; else fail
        # loud — a graph-DB backend is NOT an offline default (the offline default is the in-memory store).
        # _owns_driver: True only when WE built the driver (uri path) -> the constructor is responsible for
        # closing it if a later init step fails; an injected driver is the CALLER's to close (never touched).
        if driver is not None:
            self._driver = driver
            self._owns_driver = False
        elif uri is not None:
            self._driver = self.connect()
            self._owns_driver = True
        else:
            raise RuntimeError(
                "Neo4jGraphStore needs a uri or an injected driver — it is not an offline default "
                "(the offline default is the in-memory GraphStore; pass uri=/driver= for the paid path)."
            )
        # Ensure the uniqueness constraint, then seed _seq from the DB so a 2nd instance / restart continues
        # the sequence. If either fails and WE own the driver, close it before re-raising so a failed
        # constructor doesn't strand a connection pool (an injected driver is left to the caller).
        try:
            self._ensure_constraint()  # the fake records-and-no-ops it
            self._seq = self._read_max_seq() + 1  # -1 when empty -> start at 0
        except Exception:
            if self._owns_driver:
                self.close()
            raise

    # -- driver / lifecycle ------------------------------------------------
    def connect(self) -> Any:
        """Lazily import ``neo4j`` and build a real Bolt driver. Fail loud if it can't be imported.

        Mirrors :class:`VoyageEmbedder`'s missing-key discipline: a set ``uri`` with no importable
        ``neo4j`` raises a clear :class:`RuntimeError` rather than silently falling back to an offline
        store (which would mislabel an offline run as a paid graph run). ``import neo4j`` lives ONLY here,
        so importing this module touches no third-party package (offline/CI path stays clean).
        """
        try:
            import neo4j  # lazy: the ONLY import of neo4j in this module
        except Exception as exc:  # ImportError (absent) or any import-time failure
            raise RuntimeError(
                f"Neo4jGraphStore(uri={self.uri!r}) requires the 'neo4j' package, which is not importable "
                "(no offline fallback — use the in-memory GraphStore for offline runs, or inject a driver)."
            ) from exc
        return neo4j.GraphDatabase.driver(self.uri, auth=self._auth())

    def _auth(self) -> Any:
        # Carried separately so connect() can read it; kept tiny so a subclass/fake path is easy.
        return getattr(self, "_auth_value", None)

    def _session(self):
        # Reads (get/all/search) hit Neo4j live — there is no in-RAM cache to serve a closed store, so a
        # post-close read FAILS LOUD here (write/delete already guard with their own message before this).
        if self._closed or self._driver is None:
            raise RuntimeError("operation on a closed Neo4jGraphStore")
        return self._driver.session(database=self._database)

    def _ensure_constraint(self) -> None:
        """Emit the item_id uniqueness constraint once (idempotent; the fake records-and-no-ops it)."""
        with self._session() as session:
            session.execute_write(lambda tx: tx.run(_CONSTRAINT))

    def _read_max_seq(self) -> int:
        """Highest ``seq`` already in the DB (``-1`` when empty) so the constructor can continue the
        sequence on a 2nd instance / restart over the same DB (else a from-0 seq collides + reorders all()).

        NOTE — two CONCURRENT live instances sharing one DB both seed ``_seq`` from the SAME max, so their
        interleaved new-node seqs can tie; ``all()`` order between such interleaved nodes is then best-effort
        (the same unspecified-order property ``SqliteVectorStore.all()`` has — consumers collapse by
        ``item_id``). The sequential restart case (one instance, then another over the closed DB) is exact.
        """
        with self._session() as session:
            rows = session.execute_read(lambda tx: list(tx.run(_MAX_SEQ)))
        for record in rows:
            m = record["m"]
            return int(m) if m is not None else -1
        return -1

    def close(self) -> None:
        """Close the driver (defensively — only if it exposes ``.close()``) and mark the store closed.

        Post-close ``write``/``delete`` then fail loud (mirroring the in-memory ``GraphStore`` and
        ``SqliteVectorStore``), rather than silently accepting a mutation that never reaches Neo4j.
        """
        driver = getattr(self, "_driver", None)
        if driver is not None and hasattr(driver, "close"):
            driver.close()
        self._driver = None
        self._closed = True

    def __enter__(self) -> "Neo4jGraphStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        """Upsert the node + its typed edges in a single managed write transaction (atomic).

        Parses the typed edges from ``okf_links`` BEFORE touching the transaction (a malformed
        ``okf_links`` raises here, leaving the store unchanged — matching the in-memory store's
        parse-before-mutate atomicity). Estimates ``tokens`` from content when unset (mirroring
        ``GraphStore.write``, so ``get()``/``all()`` tokens match the in-memory baseline). The node props
        carry ``metadata`` (incl. ``okf_links``, the edge SSOT); the insertion ``seq`` rides as its own
        ``ON CREATE``-scoped parameter so ``all()`` reproduces insertion order AND a rewrite never reorders.
        """
        if self._closed:
            raise RuntimeError("write() on a closed Neo4jGraphStore")
        tokens = item.tokens
        if tokens <= 0 and item.content:
            tokens = _estimate_tokens(item.content)
        node = replace(item, tokens=tokens) if tokens != item.tokens else item
        edges = _parse_edges(node)  # raises on malformed okf_links BEFORE any mutation
        props = self._props(node)
        seq = self._seq  # the seq this write would stamp IF the node is new (ON CREATE)

        def _txn(tx: Any) -> None:
            tx.run(_NODE_MERGE, parameters={"item_id": node.item_id, "props": props, "seq": seq})
            for tgt, rel in edges:
                tx.run(_REL_MERGE, parameters={"src": node.item_id, "tgt": tgt, "rel": rel})

        with self._session() as session:
            session.execute_write(_txn)
        # Advance AFTER a successful commit. A rewrite consumes a seq it doesn't use (ON CREATE kept the
        # node's original seq) -> a harmless gap; insertion order is still strictly increasing + unique.
        self._seq += 1

    def _props(self, item: MemoryItem) -> dict:
        """The node property map. Complex fields (tags, metadata) are JSON strings — Neo4j stores scalars
        and lists of scalars, not nested maps; JSON keeps the round-trip lossless and the okf_links SSOT
        intact. JSON-encoding metadata also surfaces a non-serializable payload as a loud ``TypeError`` at
        write time (parity with the SQLite seam's atomic-write contract).

        ``embedding`` is deliberately NOT persisted — like ``SqliteVectorStore``/``GraphStore``, it is
        recomputed from content (an embedder/dim change must not resurrect a stale vector). ``seq`` is NOT
        here either: it rides as an ``ON CREATE``-scoped parameter on the node MERGE (create-only), so a
        rewrite preserves the node's original insertion order.
        """
        return {
            "item_id": item.item_id,
            "content": item.content,
            "timestamp": item.timestamp,
            "relevancy": item.relevancy,
            "session_id": item.session_id,
            "source": item.source,
            "tags": json.dumps(list(item.tags)),
            "tokens": item.tokens,
            "version": item.version,
            "metadata": json.dumps(item.metadata or {}),
        }

    def _row_to_item(self, props: dict) -> MemoryItem:
        """Reconstruct a :class:`MemoryItem` from a node's props (inverse of :meth:`_props`).

        Numeric fields are coerced (``float``/``int``) with a default for a missing/``None`` value — a real
        Neo4j node could carry a null or a numeric-string property, and ``MemoryItem`` declares ``float``/
        ``int``; coercion keeps the round-trip total instead of leaking a ``None`` into a typed field.

        ``tags``/``metadata`` are read as JSON strings (how :meth:`_props` writes them) BUT also tolerate
        a NATIVE list/map — a future external/Phase-B writer might store ``tags`` as a native list of
        scalars and ``metadata`` as a native map (both legal Neo4j property shapes). ``json.loads`` only a
        string; otherwise coerce the native value — so the read never crashes on a non-string prop.
        """
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
        with self._session() as session:
            rows = session.execute_read(
                lambda tx: list(tx.run(_GET_MATCH, parameters={"id": item_id}))
            )
        for record in rows:
            return self._row_to_item(record["n"])
        return None

    def all(self) -> list:
        """All nodes in insertion order (``ORDER BY n.seq``), reconstructed as ``MemoryItem``s."""
        with self._session() as session:
            rows = session.execute_read(lambda tx: list(tx.run(_ALL_MATCH)))
        return [self._row_to_item(record["n"]) for record in rows]

    def search(self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any) -> list:
        """Retrieve via the in-memory ``GraphStore`` for EXACT id+order parity (the golden rule).

        Pulls the as_of-visible nodes out of Neo4j (the ``$as_of`` bound is pushed into Cypher — a real
        no-leak filter, not Python-only), reconstructs their ``MemoryItem``s, builds a TRANSIENT
        ``GraphStore(max_depth=self._max_depth, embed=self._embed)`` from them, writes each item into it,
        and delegates to ``transient.search(...)``. Edges are rebuilt from each node's ``okf_links`` inside
        the transient store (the SSOT). Delegation — not a reimplementation — is what guarantees the
        identical id-set + order the parity floor requires. ``as_of`` is honored again by the transient
        search (identical result either way); per-call kwargs (e.g. ``max_depth``) pass straight through.
        """
        with self._session() as session:
            rows = session.execute_read(
                lambda tx: list(tx.run(_SEARCH_MATCH, parameters={"as_of": as_of}))
            )
        items = [self._row_to_item(record["n"]) for record in rows]

        transient = GraphStore(max_depth=self._max_depth, embed=self._embed)
        for item in items:
            transient.write(item)
        return transient.search(query, k=k, as_of=as_of, **kwargs)

    def delete(self, item_id: str) -> bool:
        """``DETACH DELETE`` the node (and its relationships). Idempotent (absent id -> ``False``)."""
        if self._closed:
            raise RuntimeError("delete() on a closed Neo4jGraphStore")
        with self._session() as session:
            count = session.execute_write(lambda tx: _delete_count(tx, item_id))
        return bool(count)


# --------------------------------------------------------------------------- #
# Edge parsing — REUSE the in-memory graph store's exact semantics (parity by construction). Re-deriving
# the (target, relation) pairs any other way could drift the typed edges from what the transient GraphStore
# rebuilds on read; instead we lean on a throwaway GraphStore instance's own parser.
# --------------------------------------------------------------------------- #
def _parse_edges(item: MemoryItem) -> list:
    """Typed out-edges ``[(target_id, relation)]`` from ``item.okf_links`` — via ``GraphStore._parse_edges``
    so the typed relationships Neo4j MERGEs match the edges the transient store rebuilds on read EXACTLY.
    Raises on a malformed ``okf_links`` (e.g. a non-iterable), before any DB mutation — atomic write.
    """
    return GraphStore(max_depth=0)._parse_edges(item)


def _delete_count(tx: Any, item_id: str) -> int:
    result = tx.run(_DELETE, parameters={"id": item_id})
    record = result.single() if hasattr(result, "single") else None
    if record is None:
        for record in result:  # fallback: drivers that return an iterable without single()
            break
    return int(record["c"]) if record is not None else 0


__all__ = ["Neo4jGraphStore"]
