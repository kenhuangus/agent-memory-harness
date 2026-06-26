"""Falkor parity eval — the ``host=``/``url=`` graph-DB seam reproduces the in-memory ``GraphStore`` EXACTLY.

Owner: Brent (@bgibson1618). EVAL-FIRST: written before the backend it gates (RED until
``memeval.stores.falkor_store.FalkorGraphStore`` exists).

**Phase A — a parity FLOOR.** ``FalkorGraphStore`` (over the Redis/openCypher client) must reproduce the in-memory
``GraphStore``'s retrieval **id-set AND order EXACTLY**, proving the port is faithful. FalkorDB is a no-op
on accuracy *for now*; PR2 later adds FalkorDB-native graph traversal and this parity floor is its
regression guard. Parity is achieved BY CONSTRUCTION: ``FalkorGraphStore.search`` pulls the as_of-visible
nodes out of FalkorDB and delegates scoring/BFS/tie-break to a TRANSIENT in-memory ``GraphStore`` built from
those nodes — it does NOT reimplement seeding/BFS/scoring, so ids+order cannot diverge.

**No real FalkorDB, no network.** A committed stdlib ``FakeFalkorClient`` / ``FakeGraph`` behaves
like a tiny graph store for OUR known Cypher shapes and RECORDS every emitted ``(cypher, params)`` so the
tests can assert the wire shape (a node ``MERGE … SET n +=`` per write and NO relationship ``[r:REL`` merge
— Phase A writes nodes + ``okf_links`` only, the native typed graph is deferred to Phase B — a
``MATCH (n:Memory)`` on search with the ``$as_of`` bound pushed to Cypher, ``DETACH DELETE`` on
delete). The fake also models FalkorDB endpoint-creation, so a reintroduced relationship write surfaces a
read-visible placeholder and fails the parity guards. ``falkordb`` is NOT installed and the offline/CI path
must never import it.

**ANTI-THEATER.** Every parity case asserts the Falkor-backed store returns the byte-identical ordered id
list as an in-memory ``GraphStore`` baseline fed the SAME writes — a silent edge mis-resolution in the
Cypher round-trip FAILS the test. One case strips the connecting ``okf_links`` edge and proves the gold
becomes unreachable from BOTH stores (recovery is traversal, not lexical). as_of is proven both ways: a
future node is excluded AND the ``$as_of`` parameter is recorded on the search ``MATCH`` (the bound was
pushed to Cypher, not only filtered in Python).

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_falkor_parity
"""

from __future__ import annotations

import json
import re
import unittest

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.relations import BOTH, IN, OUT, RELATES_TO


# --------------------------------------------------------------------------- #
# Fixture helpers (mirror test_graph_durability.py conventions)
# --------------------------------------------------------------------------- #
def _item(iid: str, content: str, links=None, ts: float = 0.0) -> MemoryItem:
    """A MemoryItem with an OKF title + optional typed ``okf_links`` = ``[[anchor, target], …]``.

    An ``okf_links`` entry ``["depends on", "beta"]`` is the ``(anchor, target)`` tuple the graph store
    classifies into a typed ``depends_on`` edge ``(this_node -> beta)`` — exactly the durability test's
    edge format.
    """
    md = {"okf_title": iid}
    if links is not None:
        md["okf_links"] = links
    return MemoryItem(item_id=iid, content=content, timestamp=ts, metadata=md)


def _ids(hits) -> list:
    return [h.item_id for h in hits]


# A coined-token corpus mirroring test_graph_retrieval_evals: query verbs (depend/conflict/call/related)
# are ABSENT from node content, so a gold/neighbor token only seeds itself and is reached ONLY via links —
# making the parity assertion meaningful (lexical recovery would mask a broken edge round-trip).
CORPUS = (
    # typed_direction: Vortex --depends_on--> Zephyr --depends_on--> Quasar
    _item("td-zephyr", "Zephyr orchestrates ingestion.", [["depends on", "td-quasar"]], ts=1.0),
    _item("td-quasar", "Quasar persists partitions.", ts=2.0),
    _item("td-vortex", "Vortex schedules batches.", [["depends on", "td-zephyr"]], ts=3.0),
    # relation_disambiguation: Hub --depends_on-->Alpha, --conflicts_with-->Beta, --calls-->Gamma
    _item("rd-hub", "Hub coordinates flows.",
          [["depends on", "rd-alpha"], ["conflicts with", "rd-beta"], ["calls", "rd-gamma"]], ts=4.0),
    _item("rd-alpha", "Alpha keeps ledgers.", ts=5.0),
    _item("rd-beta", "Beta mirrors archives.", ts=6.0),
    _item("rd-gamma", "Gamma scores anomalies.", ts=7.0),
    # multi_hop chain: Apex->Bravo->Charlie->Delta (Delta at depth 3, beyond the depth-2 default)
    _item("mh-apex", "Apex receives sessions.", [["calls", "mh-bravo"]], ts=8.0),
    _item("mh-bravo", "Bravo validates payloads.", [["calls", "mh-charlie"]], ts=9.0),
    _item("mh-charlie", "Charlie reserves stock.", [["calls", "mh-delta"]], ts=10.0),
    _item("mh-delta", "Delta commits writes.", ts=11.0),
    # untyped_fallback: Solis --relates_to--> Luna ; Nimbus --relates_to--> Stratus
    _item("uf-solis", "Solis indexes documents.", [["relates to", "uf-luna"]], ts=12.0),
    _item("uf-luna", "Luna ranks candidates.", ts=13.0),
    _item("uf-nimbus", "Nimbus caches fragments.", [["relates to", "uf-stratus"]], ts=14.0),
    _item("uf-stratus", "Stratus compresses blobs.", ts=15.0),
    # inert haystack (coined, link-free, disjoint tokens) so k is selective
    _item("noise-mistral", "Mistral rotates credentials.", ts=16.0),
    _item("noise-cobalt", "Cobalt aggregates metrics.", ts=17.0),
    _item("noise-onyx", "Onyx throttles producers.", ts=18.0),
    _item("noise-jade", "Jade encrypts volumes.", ts=19.0),
    _item("noise-saffron", "Saffron buffers streams.", ts=20.0),
    _item("noise-indigo", "Indigo dedupes records.", ts=21.0),
)


# --------------------------------------------------------------------------- #
# FakeFalkorClient / FakeGraph — a stdlib stand-in for the falkordb client.
#
# It interprets OUR known Cypher shapes by substring (NOT a general Cypher engine):
#   * a node MERGE+`SET n +=`        -> ON CREATE: new id gets props + $seq; existing id merges props,
#                                       PRESERVES its seq (a rewrite must not reorder all())
#   * a relationship MERGE `[r:REL`  -> FAITHFUL endpoint-creation: a MERGE on an ABSENT :Memory endpoint
#                                       CREATES it as a bare placeholder (no props/seq) — real Falkor does
#                                       this, so a reintroduced node-creating rel write surfaces a visible
#                                       placeholder and FAILS the no-placeholder/parity guards. Phase A no
#                                       longer emits this (dormant), but it is modeled honestly.
#   * a `MATCH (n:Memory)` read       -> yield a node row per node, honoring the $as_of and single-id filters
#   * a max(n.seq) read               -> the highest seq (constructor seq continuation), -1 when empty
#   * a `DETACH DELETE`               -> pop the id, set result.nodes_deleted
#   * a CREATE INDEX call             -> no-op
# Every (cypher, merged_params) is appended to self.calls so tests can assert the wire shape.
# --------------------------------------------------------------------------- #
class _FakeNode:
    """A falkordb-node-like cell exposing ``.properties``."""

    def __init__(self, properties: dict) -> None:
        self.properties = properties


class _FakeQueryResult:
    """The small QueryResult surface consumed by FalkorGraphStore."""

    def __init__(self, result_set: list, *, nodes_deleted: int = 0) -> None:
        self.result_set = result_set
        self.nodes_deleted = nodes_deleted


class FakeGraph:
    """FalkorDB graph surface: ``query`` and ``ro_query`` interpreted by substring."""

    def __init__(self, client: "FakeFalkorClient") -> None:
        self._client = client

    def query(self, cypher: str, params=None, timeout=None):
        return self._run(cypher, params=params)

    def ro_query(self, cypher: str, params=None, timeout=None):
        return self._run(cypher, params=params)

    def _run(self, cypher: str, params=None):
        params = dict(params or {})
        self._client.calls.append((cypher, params))
        nodes = self._client.nodes
        rels = self._client.rels

        if "DETACH DELETE" in cypher:
            removed = nodes.pop(params["id"], None)
            rels.difference_update({edge for edge in rels if params["id"] in edge[:2]})
            return _FakeQueryResult([], nodes_deleted=1 if removed is not None else 0)

        if "CREATE INDEX" in cypher:
            return _FakeQueryResult([])

        # Max-seq read (constructor): check before the generic node-read branch.
        if "max(n.seq)" in cypher:
            seqs = [p.get("seq") for p in nodes.values() if p.get("seq") is not None]
            return _FakeQueryResult([[max(seqs) if seqs else -1]])

        # Node upsert: model `ON CREATE`, preserving seq across rewrites.
        if "SET n +=" in cypher:
            iid = params["item_id"]
            if iid in nodes:
                preserved_seq = nodes[iid].get("seq")
                merged = dict(params["props"])
                merged["seq"] = preserved_seq
                nodes[iid] = merged
            else:
                created = dict(params["props"])
                created["seq"] = params.get("seq")
                nodes[iid] = created
            return _FakeQueryResult([])

        if "DELETE r" in cypher and "[r:REL" in cypher:
            rels.clear()
            return _FakeQueryResult([])

        if "UNWIND $edges AS e" in cypher and "MATCH (a:Memory" in cypher and "MATCH (b:Memory" in cypher:
            for edge in params.get("edges", ()):
                src, tgt, rel = edge["src"], edge["tgt"], edge["rel"]
                if src in nodes and tgt in nodes:
                    rels.add((src, tgt, rel))
            return _FakeQueryResult([])

        if "MATCH p=(s:Memory)" in cypher and "[:REL*1.." in cypher:
            return _FakeQueryResult(self._traverse(cypher, params))

        # Relationship / endpoint write branch. PR1 must never emit this, but if it does the fake models
        # endpoint placeholder creation so the forward-ref parity tests fail loudly.
        if "[r:REL" in cypher or ("MERGE" in cypher and cypher.count(":Memory {item_id") > 1):
            if "edges" in params:
                endpoints = []
                for edge in params.get("edges", ()):
                    endpoints.extend((edge.get("src"), edge.get("tgt")))
            else:
                endpoints = [params.get("src"), params.get("tgt")]
            for endpoint in endpoints:
                if endpoint is not None and endpoint not in nodes:
                    nodes[endpoint] = {"item_id": endpoint}
            return _FakeQueryResult([])

        if "MATCH (n:Memory" in cypher and "RETURN n" in cypher:
            target_id = params.get("id")
            as_of = params.get("as_of")
            rows = []
            for iid, props in nodes.items():
                if target_id is not None and iid != target_id:
                    continue
                if as_of is not None and props.get("timestamp") is not None and props["timestamp"] > as_of:
                    continue
                rows.append((iid, props))
            if "ORDER BY n.seq" in cypher:
                rows.sort(key=lambda r: r[1].get("seq", 0))
            return _FakeQueryResult([[_FakeNode(props)] for _, props in rows])

        return _FakeQueryResult([])

    def _traverse(self, cypher: str, params: dict) -> list:
        depth = int(re.search(r"\*1\.\.(\d+)", cypher).group(1))
        if "<-[:REL" in cypher:
            direction = IN
        elif "]->(m:Memory)" in cypher:
            direction = OUT
        else:
            direction = BOTH

        seed_ids = list(params.get("seed_ids", ()))
        rel = params.get("rel")
        as_of = params.get("as_of")
        totals: dict[tuple[str, str], float] = {}

        def visible(nid: str) -> bool:
            props = self._client.nodes.get(nid)
            if props is None:
                return False
            return not (as_of is not None and props.get("timestamp") is not None and props["timestamp"] > as_of)

        def allowed(edge_rel: str) -> bool:
            return rel == RELATES_TO or edge_rel == rel or edge_rel == RELATES_TO

        def edge_weight(edge_rel: str) -> float:
            return float(params.get(f"w_{edge_rel}", params.get("w_default", 0.5)))

        def neighbors(nid: str) -> list:
            out = []
            for src, tgt, edge_rel in sorted(self._client.rels):
                if not allowed(edge_rel):
                    continue
                edge = (src, tgt, edge_rel)
                if direction in (OUT, BOTH) and src == nid:
                    out.append((tgt, edge, edge_rel))
                if direction in (IN, BOTH) and tgt == nid:
                    out.append((src, edge, edge_rel))
            return out

        def walk(seed: str, nid: str, used: set, weight: float, dist: int) -> None:
            if dist >= depth:
                return
            for nxt, edge, edge_rel in neighbors(nid):
                if edge in used or not visible(nxt):
                    continue
                path_weight = weight * edge_weight(edge_rel)
                totals[(nxt, seed)] = totals.get((nxt, seed), 0.0) + path_weight
                walk(seed, nxt, used | {edge}, path_weight, dist + 1)

        for seed in seed_ids:
            if visible(seed):
                walk(seed, seed, set(), 1.0, 0)
        return [[item_id, seed_id, weight] for (item_id, seed_id), weight in sorted(totals.items())]


class FakeFalkorClient:
    """A tiny in-RAM stand-in for ``falkordb.FalkorDB(...)``. NO real falkordb, NO network."""

    def __init__(self) -> None:
        self.nodes: dict = {}     # item_id -> props dict (content/timestamp/.../metadata json/seq)
        self.rels: set = set()    # (src, tgt, rel_type) materialized native [:REL] relationships
        self.calls: list = []     # [(cypher, merged_params)] — every emitted statement, for shape asserts
        self.closed = False

    def select_graph(self, name: str) -> FakeGraph:
        return FakeGraph(self)

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Import the backend under test. EVAL-FIRST: this raises until falkor_store exists,
# which is exactly the RED state. We import inside a helper so the import error is
# attributed to each test (clear RED) rather than to module collection.
# --------------------------------------------------------------------------- #
def _FalkorGraphStore():
    from memeval.stores.falkor_store import FalkorGraphStore
    return FalkorGraphStore


def _baseline(*, max_depth: int = 2, strip_links: bool = False) -> GraphStore:
    g = GraphStore(max_depth=max_depth)
    for it in CORPUS:
        g.write(_strip(it) if strip_links else it)
    return g


def _falkor(*, max_depth: int = 2, strip_links: bool = False):
    FalkorGraphStore = _FalkorGraphStore()
    client = FakeFalkorClient()
    s = FalkorGraphStore(client=client, max_depth=max_depth)
    for it in CORPUS:
        s.write(_strip(it) if strip_links else it)
    return s, client


def _strip(it: MemoryItem) -> MemoryItem:
    md = dict(it.metadata or {})
    md["okf_links"] = []
    return MemoryItem(item_id=it.item_id, content=it.content, timestamp=it.timestamp, metadata=md)


# --------------------------------------------------------------------------- #
# 1. Fail-loud seam (mirrors VoyageEmbedder's missing-key RuntimeError discipline)
# --------------------------------------------------------------------------- #
class FailLoudTests(unittest.TestCase):
    def test_target_without_falkordb_raises(self) -> None:
        # falkordb is NOT installed; a set target with no usable client must fail loud, not silently no-op.
        FalkorGraphStore = _FalkorGraphStore()
        with self.assertRaises(RuntimeError):
            FalkorGraphStore(host="localhost", port=6379)

    def test_no_target_and_no_client_raises(self) -> None:
        # A graph-DB backend needs host=/url= or an injected client — it is NOT an offline default
        # (the offline default remains the in-memory GraphStore).
        FalkorGraphStore = _FalkorGraphStore()
        with self.assertRaises(RuntimeError):
            FalkorGraphStore()

    def test_no_module_load_import_of_falkordb(self) -> None:
        # The offline path must never import falkordb/redis: importing the module must not pull them in.
        import importlib
        import sys

        sys.modules.pop("memeval.stores.falkor_store", None)
        importlib.import_module("memeval.stores.falkor_store")
        self.assertNotIn("falkordb", sys.modules,
                         "importing falkor_store must NOT import falkordb (offline/CI path stays clean)")
        self.assertNotIn("redis", sys.modules,
                         "importing falkor_store must NOT import redis (pulled by falkordb)")

    def test_connect_forwards_host_port_and_password_to_client(self) -> None:
        # Inject a fake `falkordb` module so connect() succeeds offline and captures what it was handed.
        import sys
        import types

        captured: dict = {}

        class _FakeFalkorDB(FakeFalkorClient):
            def __init__(self, host=None, port=None, password=None, **kwargs):
                super().__init__()
                captured["host"] = host
                captured["port"] = port
                captured["password"] = password
                captured["kwargs"] = kwargs

            @classmethod
            def from_url(cls, url):
                captured["url"] = url
                return cls()

        fake = types.ModuleType("falkordb")
        fake.FalkorDB = _FakeFalkorDB  # type: ignore[attr-defined]
        saved = sys.modules.get("falkordb")
        sys.modules["falkordb"] = fake
        try:
            FalkorGraphStore = _FalkorGraphStore()
            FalkorGraphStore(host="db.local", port=6380, password="secret")
        finally:
            if saved is not None:
                sys.modules["falkordb"] = saved
            else:
                sys.modules.pop("falkordb", None)
        self.assertEqual(captured.get("host"), "db.local")
        self.assertEqual(captured.get("port"), 6380)
        self.assertEqual(captured.get("password"), "secret")


# --------------------------------------------------------------------------- #
# 2-4. Parity across the typed/disambiguation/multi-hop/untyped slices
# --------------------------------------------------------------------------- #
class ParityTests(unittest.TestCase):
    def _assert_parity(self, query: str, *, k: int = 5, max_depth: int = 2, as_of=None) -> list:
        """The Falkor-backed store returns the BYTE-IDENTICAL ordered id list as the in-memory baseline."""
        base = _baseline(max_depth=max_depth)
        store, _client = _falkor(max_depth=max_depth)
        base_ids = _ids(base.search(query, k=k, as_of=as_of))
        neo_ids = _ids(store.search(query, k=k, as_of=as_of))
        self.assertEqual(neo_ids, base_ids,
                         f"parity broken for {query!r}: falkor={neo_ids} != baseline={base_ids}")
        return neo_ids

    def test_typed_direction_out_parity(self) -> None:
        # "Zephyr dependency" = OUT -> Quasar (not Vortex, the IN dependent).
        ids = self._assert_parity("Zephyr dependency")
        self.assertIn("td-quasar", ids)

    def test_typed_direction_in_parity(self) -> None:
        # "Zephyr dependents" = IN -> Vortex (not Quasar, the OUT target).
        ids = self._assert_parity("Zephyr dependents")
        self.assertIn("td-vortex", ids)

    def test_relation_disambiguation_conflict_parity(self) -> None:
        ids = self._assert_parity("Hub conflict")
        self.assertIn("rd-beta", ids)

    def test_relation_disambiguation_depends_parity(self) -> None:
        ids = self._assert_parity("Hub dependency")
        self.assertIn("rd-alpha", ids)

    def test_relation_disambiguation_calls_parity(self) -> None:
        ids = self._assert_parity("Hub callee")
        self.assertIn("rd-gamma", ids)

    def test_multi_hop_depth3_parity(self) -> None:
        # A max_depth=3 store reaches the depth-3 gold (Delta). Parity must hold at the deeper knob too.
        ids = self._assert_parity("Apex chain tail", max_depth=3)
        self.assertIn("mh-delta", ids)
        # And at the default depth-2 the tail is unreachable from BOTH (identical miss).
        ids2 = self._assert_parity("Apex chain tail", max_depth=2)
        self.assertNotIn("mh-delta", ids2)

    def test_untyped_fallback_parity(self) -> None:
        ids = self._assert_parity("Solis related")
        self.assertIn("uf-luna", ids)
        ids2 = self._assert_parity("Nimbus related")
        self.assertIn("uf-stratus", ids2)


# --------------------------------------------------------------------------- #
# 5. as_of parity + NO-LEAK (and the $as_of bound is pushed to Cypher)
# --------------------------------------------------------------------------- #
class AsOfTests(unittest.TestCase):
    def test_as_of_excludes_future_node_parity(self) -> None:
        base = _baseline()
        store, client = _falkor()
        # mh-delta has ts=11.0; an as_of=10.5 must exclude it from BOTH stores.
        base_ids = _ids(base.search("Delta commits", k=5, as_of=10.5))
        neo_ids = _ids(store.search("Delta commits", k=5, as_of=10.5))
        self.assertEqual(neo_ids, base_ids)
        self.assertNotIn("mh-delta", neo_ids, "future node excluded by as_of in the falkor-backed store")

    def test_as_of_pushed_to_cypher(self) -> None:
        # NO-LEAK proof: the bound is enforced in Cypher (MATCH carries $as_of), not only in Python.
        store, client = _falkor()
        client.calls.clear()
        store.search("Delta commits", k=5, as_of=10.5)
        match_calls = [(c, p) for (c, p) in client.calls if "MATCH (n:Memory)" in c and "RETURN n" in c]
        self.assertTrue(match_calls, "search must emit a MATCH (n:Memory) … RETURN n read")
        self.assertTrue(any("as_of" in p for (_c, p) in match_calls),
                        "the $as_of bound must be a parameter on the search MATCH (pushed to Cypher)")
        self.assertTrue(any("$as_of" in c for (c, _p) in match_calls),
                        "the search Cypher must reference $as_of (a real no-leak bound, not Python-only)")


# --------------------------------------------------------------------------- #
# 6. CRUD round-trip + post-close fail-loud
# --------------------------------------------------------------------------- #
class CrudTests(unittest.TestCase):
    def test_write_get_all_order_delete(self) -> None:
        FalkorGraphStore = _FalkorGraphStore()
        base = GraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())
        for it in (_item("a", "a", ts=1.0), _item("b", "b", ts=2.0), _item("c", "c", ts=3.0)):
            base.write(it)
            store.write(it)

        # get parity
        self.assertEqual(store.get("b").content, base.get("b").content)
        self.assertIsNone(store.get("missing"))

        # all() preserves insertion order (matches baseline)
        self.assertEqual([i.item_id for i in store.all()], [i.item_id for i in base.all()])
        self.assertEqual([i.item_id for i in store.all()], ["a", "b", "c"])

        # delete returns bool, is idempotent, matches baseline
        self.assertTrue(store.delete("b"))
        self.assertTrue(base.delete("b"))
        self.assertFalse(store.delete("b"), "second delete of the same id is False (idempotent)")
        self.assertFalse(store.delete("never"))
        self.assertEqual([i.item_id for i in store.all()], [i.item_id for i in base.all()])
        self.assertIsNone(store.get("b"))

    def test_all_fields_round_trip(self) -> None:
        FalkorGraphStore = _FalkorGraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())
        store.write(MemoryItem(item_id="x1", content="payload", timestamp=7.5, relevancy=0.3,
                               session_id="sess-9", source="unit", tags=["a", "b"], tokens=42,
                               version=4, metadata={"okf_title": "x1", "k": "v"}))
        got = store.get("x1")
        self.assertEqual(
            (got.content, got.timestamp, got.relevancy, got.session_id, got.source,
             got.tags, got.tokens, got.version, got.metadata.get("k")),
            ("payload", 7.5, 0.3, "sess-9", "unit", ["a", "b"], 42, 4, "v"),
            "every PERSISTED MemoryItem field round-trips through the Falkor props "
            "(embedding is excluded BY DESIGN — recomputed from content like SqliteVectorStore/GraphStore)")

    def test_post_close_write_and_delete_fail_loud(self) -> None:
        FalkorGraphStore = _FalkorGraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())
        store.write(_item("a", "a", ts=1.0))
        store.close()
        with self.assertRaises(RuntimeError):
            store.write(_item("b", "b", ts=2.0))
        with self.assertRaises(RuntimeError):
            store.delete("a")
        # Reads hit Falkor live (no in-RAM cache) -> a post-close read must FAIL LOUD too, not deref a
        # nulled client with an AttributeError.
        with self.assertRaises(RuntimeError):
            store.search("a", k=1)
        with self.assertRaises(RuntimeError):
            store.get("a")
        with self.assertRaises(RuntimeError):
            store.all()

    def test_context_manager_closes_client(self) -> None:
        FalkorGraphStore = _FalkorGraphStore()
        client = FakeFalkorClient()
        with FalkorGraphStore(client=client) as store:
            store.write(_item("a", "a", ts=1.0))
        self.assertTrue(client.closed, "__exit__ closed the client")

    def test_tokens_estimated_when_zero_parity(self) -> None:
        # An item written with tokens=0 + content gets an ESTIMATE persisted (mirrors GraphStore.write),
        # so get()/all() tokens match the in-memory baseline instead of round-tripping a bare 0.
        FalkorGraphStore = _FalkorGraphStore()
        base = GraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())
        it = _item("tok", "some content with several words here", ts=1.0)
        base.write(it)
        store.write(it)
        self.assertGreater(store.get("tok").tokens, 0, "tokens estimated from content, not left at 0")
        self.assertEqual(store.get("tok").tokens, base.get("tok").tokens,
                         "token estimate matches the in-memory baseline (same _estimate_tokens)")


# --------------------------------------------------------------------------- #
# 6b. seq ordering — create-only + DB-derived (all() insertion order survives rewrite + restart)
# --------------------------------------------------------------------------- #
class SeqOrderingTests(unittest.TestCase):
    def test_rewrite_preserves_insertion_order(self) -> None:
        # Write a,b,c then RE-WRITE b with new content. all() order must stay (a,b,c) — a rewrite must NOT
        # bump b to the tail (seq is ON CREATE only). Parity: the in-memory baseline also keeps (a,b,c).
        FalkorGraphStore = _FalkorGraphStore()
        base = GraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())
        for it in (_item("a", "a", ts=1.0), _item("b", "b-orig", ts=2.0), _item("c", "c", ts=3.0)):
            base.write(it)
            store.write(it)
        base.write(_item("b", "b-rewritten", ts=9.0))
        store.write(_item("b", "b-rewritten", ts=9.0))
        self.assertEqual([i.item_id for i in store.all()], [i.item_id for i in base.all()])
        self.assertEqual([i.item_id for i in store.all()], ["a", "b", "c"],
                         "rewriting b keeps its original seq -> insertion order (a,b,c) preserved")
        self.assertEqual(store.get("b").content, "b-rewritten", "the rewrite content landed")

    def test_restart_continues_seq_from_db(self) -> None:
        # Two stores sharing ONE client model a restart/2nd-instance over the SAME DB: store1 writes a,b;
        # store2 (re-init over the same client) writes c. all() order must be (a,b,c) — store2 must derive
        # its starting seq from the DB max, not from 0 (which would collide a's/b's seq and scramble order).
        FalkorGraphStore = _FalkorGraphStore()
        client = FakeFalkorClient()
        store1 = FalkorGraphStore(client=client)
        store1.write(_item("a", "a", ts=1.0))
        store1.write(_item("b", "b", ts=2.0))
        store2 = FalkorGraphStore(client=client)  # "restart" over the same DB
        store2.write(_item("c", "c", ts=3.0))
        self.assertEqual([i.item_id for i in store2.all()], ["a", "b", "c"],
                         "store2 continued the seq from the DB max -> insertion order survives restart")


# --------------------------------------------------------------------------- #
# 6c. Constructor robustness — a failed index must not strand a client the constructor OWNS;
#     _row_to_item tolerates native (non-JSON-string) props a future Phase-B/external writer may emit.
# --------------------------------------------------------------------------- #
class _RaisingIndexClient(FakeFalkorClient):
    """A client whose index write RAISES — to prove the constructor cleans up after itself."""

    def select_graph(self, name: str) -> FakeGraph:
        return _RaisingIndexGraph(self)


class _RaisingIndexGraph(FakeGraph):
    def query(self, cypher: str, params=None, timeout=None):
        if "CREATE INDEX" in cypher:
            raise RuntimeError("index creation failed (simulated)")
        return super().query(cypher, params=params, timeout=timeout)


class ConstructorRobustnessTests(unittest.TestCase):
    def test_index_failure_closes_owned_client(self) -> None:
        # host/url path: the constructor BUILT the client, so a failed index must close it before
        # re-raising — a failed __init__ must not strand a connection pool.
        import sys
        import types

        built = {}

        class _FakeFalkorDB(_RaisingIndexClient):
            def __init__(self, *args, **kwargs):
                super().__init__()
                built["client"] = self

            @classmethod
            def from_url(cls, url):
                return cls()

        fake = types.ModuleType("falkordb")
        fake.FalkorDB = _FakeFalkorDB  # type: ignore[attr-defined]
        saved = sys.modules.get("falkordb")
        sys.modules["falkordb"] = fake
        try:
            FalkorGraphStore = _FalkorGraphStore()
            with self.assertRaises(RuntimeError):
                FalkorGraphStore(host="localhost", port=6379)
        finally:
            if saved is not None:
                sys.modules["falkordb"] = saved
            else:
                sys.modules.pop("falkordb", None)
        self.assertTrue(built["client"].closed,
                        "a constructor-owned client must be closed when the index ensure fails")

    def test_index_failure_does_not_close_injected_client(self) -> None:
        # injected path: the CALLER owns the client, so the constructor must NOT close it on failure.
        FalkorGraphStore = _FalkorGraphStore()
        client = _RaisingIndexClient()
        with self.assertRaises(RuntimeError):
            FalkorGraphStore(client=client)
        self.assertFalse(client.closed,
                         "an injected (caller-owned) client must NOT be closed by a failed __init__")

    def test_row_to_item_tolerates_native_props(self) -> None:
        # Phase-B-proofing: a future external/Phase-B writer might store tags as a native list and metadata
        # as a native map (Falkor supports lists-of-scalars natively). _row_to_item must not crash on those.
        FalkorGraphStore = _FalkorGraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())
        item = store._row_to_item({
            "item_id": "nat", "content": "c", "timestamp": 1.0, "relevancy": 0.5,
            "session_id": "s", "source": "src", "tags": ["x", "y"], "tokens": 3,
            "version": 2, "metadata": {"okf_title": "nat", "k": "v"},  # NATIVE list + dict, not JSON strings
        })
        self.assertEqual(item.tags, ["x", "y"])
        self.assertEqual(item.metadata.get("k"), "v")


# --------------------------------------------------------------------------- #
# 7. Cypher-shape assertions — Phase A persists :Memory NODES only (no native [:REL]; that is Phase B)
# --------------------------------------------------------------------------- #
class CypherShapeTests(unittest.TestCase):
    def test_write_emits_node_merge_and_no_rel_merge(self) -> None:
        # Phase A writes the NODE only — NO native [:REL] relationships (dropped for the Codex R1
        # placeholder-node bug). Edges live in okf_links inside the node props (the SSOT reads rebuild from).
        FalkorGraphStore = _FalkorGraphStore()
        client = FakeFalkorClient()
        store = FalkorGraphStore(client=client)
        client.calls.clear()
        store.write(_item("rd-hub", "Hub coordinates flows.",
                          [["depends on", "rd-alpha"], ["conflicts with", "rd-beta"]], ts=1.0))
        cyphers = [c for (c, _p) in client.calls]
        # (a) the node MERGE is emitted, ON CREATE-stamping seq and SET-merging props.
        self.assertTrue(
            any("MERGE" in c and "ON CREATE SET n.seq" in c and "SET n +=" in c for c in cyphers),
            "a node MERGE … ON CREATE SET n.seq … SET n += $props per write")
        # (b) NO typed relationship MERGE is emitted (Phase A persists nodes only).
        self.assertEqual([c for c in cyphers if "[r:REL" in c], [],
                         "Phase A must emit NO typed [:REL] relationship MERGE (deferred to Phase B)")
        # (c) write emits EXACTLY ONE :Memory MERGE (the node) — no second :Memory MERGE for any target
        # (a rel write would emit `MERGE (b:Memory {item_id:$tgt})`, creating a placeholder on real Falkor).
        memory_merges = [c for c in cyphers if "MERGE" in c and ":Memory {item_id" in c]
        self.assertEqual(len(memory_merges), 1,
                         "exactly one :Memory MERGE per write (the node) — no endpoint MERGE for a target")
        # the node props carry the metadata (incl. okf_links) as a JSON string — the SSOT for reads
        merge_calls = [p for (c, p) in client.calls if "SET n +=" in c]
        props = merge_calls[0]["props"]
        self.assertIn("metadata", props)
        self.assertIn("okf_links", json.loads(props["metadata"]),
                      "okf_links is fully persisted on the node — the durable edge SSOT for Phase-A reads")

    def test_search_emits_match_and_delete_emits_detach_delete(self) -> None:
        FalkorGraphStore = _FalkorGraphStore()
        client = FakeFalkorClient()
        store = FalkorGraphStore(client=client)
        store.write(_item("a", "a", ts=1.0))
        client.calls.clear()
        store.search("a", k=1)
        self.assertTrue(any("MATCH (n:Memory)" in c and "RETURN n" in c for (c, _p) in client.calls),
                        "search emits a MATCH (n:Memory) … RETURN n read")
        client.calls.clear()
        store.delete("a")
        self.assertTrue(any("DETACH DELETE" in c for (c, _p) in client.calls),
                        "delete emits DETACH DELETE")


# --------------------------------------------------------------------------- #
# 8. ANTI-THEATER — id-set/order is the contract
# --------------------------------------------------------------------------- #
class AntiTheaterTests(unittest.TestCase):
    def test_stripping_edge_makes_gold_unreachable_in_both(self) -> None:
        # If the connecting okf_links edge is stripped, the lexically-inert gold is unreachable from BOTH
        # stores -> recovery is TRAVERSAL, not lexical. (Apex->Bravo: Bravo is reachable only via the edge.)
        base_links = _baseline()
        store_links, _d1 = _falkor()
        self.assertIn("mh-bravo", _ids(store_links.search("Apex chain", k=5)),
                      "with the edge, Bravo is reached via traversal in the falkor-backed store")
        self.assertIn("mh-bravo", _ids(base_links.search("Apex chain", k=5)))

        base_nolinks = _baseline(strip_links=True)
        store_nolinks, _d2 = _falkor(strip_links=True)
        self.assertNotIn("mh-bravo", _ids(store_nolinks.search("Apex chain", k=5)),
                         "strip the edge -> Bravo unreachable from the falkor-backed store (not lexical)")
        self.assertNotIn("mh-bravo", _ids(base_nolinks.search("Apex chain", k=5)),
                         "strip the edge -> Bravo unreachable from the baseline too (parity holds)")

    def test_byte_identical_ordered_ids_across_full_corpus(self) -> None:
        # The contract: a multi-item ordered id list is byte-identical between the two stores. A silent
        # edge mis-resolution in the Cypher round-trip would reorder/drop an id and FAIL here. With the
        # FAITHFUL endpoint-modeling fake, the forward-reference links in CORPUS (e.g. td-zephyr -> td-quasar
        # written before td-quasar) are now a REAL no-placeholder guard: a node-creating rel write would
        # materialize a placeholder target and break this byte-identity.
        base = _baseline(max_depth=3)
        store, _client = _falkor(max_depth=3)
        for query in ("Zephyr dependency", "Zephyr dependents", "Hub conflict", "Hub dependency",
                      "Hub callee", "Apex chain tail", "Solis related", "Nimbus related",
                      "Quasar partitions", "Delta commits writes"):
            base_ids = _ids(base.search(query, k=5))
            neo_ids = _ids(store.search(query, k=5))
            self.assertEqual(neo_ids, base_ids,
                             f"byte-identical ordered ids required for {query!r}: "
                             f"falkor={neo_ids} != baseline={base_ids}")

    def test_forward_ref_creates_no_placeholder(self) -> None:
        # THE Codex R1 regression guard. A links to an unwritten B (forward reference). Phase A writes NODES
        # only — no [:REL] — so NO placeholder B node is ever created. Before B is written, all()/search must
        # return ONLY A (byte-identical to the in-memory baseline, which has no absent nodes); after B is
        # written, B materializes with its OWN seq so all() order is (a, b). This test genuinely exercises
        # the endpoint-modeling fake: if a node-creating rel write were reintroduced, the forward-ref MERGE
        # would create a bare placeholder B here and EVERY assertion below would fail.
        FalkorGraphStore = _FalkorGraphStore()
        base = GraphStore()
        store = FalkorGraphStore(client=FakeFalkorClient())

        a = _item("a", "a", [["depends on", "b"]], ts=1.0)  # forward reference to not-yet-written "b"
        base.write(a)
        store.write(a)

        # BEFORE b exists: only a is present in BOTH stores (no placeholder b). Byte-identical.
        self.assertEqual([i.item_id for i in store.all()], [i.item_id for i in base.all()])
        self.assertEqual([i.item_id for i in store.all()], ["a"],
                         "forward-ref link must NOT create a placeholder 'b' node (Phase A writes nodes only)")
        self.assertIsNone(store.get("b"), "the unwritten target must not exist as a placeholder")
        self.assertEqual(_ids(store.search("a", k=5)), _ids(base.search("a", k=5)),
                         "search sees only the real node a — identical to the baseline")

        # AFTER b is written: it materializes with its own seq -> all() order is (a, b), matching baseline.
        b = _item("b", "b", ts=2.0)
        base.write(b)
        store.write(b)
        self.assertEqual([i.item_id for i in store.all()], [i.item_id for i in base.all()])
        self.assertEqual([i.item_id for i in store.all()], ["a", "b"],
                         "b materializes as a real node with its own seq -> insertion order (a, b)")
        # and now the depends_on edge resolves: a -> b reachable, byte-identical to the baseline.
        self.assertEqual(_ids(store.search("a depends", k=5)), _ids(base.search("a depends", k=5)),
                         "the link resolves once b is a real node — identical traversal to the baseline")


if __name__ == "__main__":
    unittest.main()
