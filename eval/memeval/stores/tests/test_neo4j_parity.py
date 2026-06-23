"""Neo4j parity eval — the ``uri=`` graph-DB seam reproduces the in-memory ``GraphStore`` EXACTLY.

Owner: Brent (@bgibson1618). EVAL-FIRST: written before the backend it gates (RED until
``memeval.stores.neo4j_store.Neo4jGraphStore`` exists).

**Phase A — a parity FLOOR.** ``Neo4jGraphStore`` (over the Bolt driver) must reproduce the in-memory
``GraphStore``'s retrieval **id-set AND order EXACTLY**, proving the port is faithful. Neo4j is a no-op on
accuracy *for now*; Phase B later adds Neo4j-native accuracy (Cypher/GDS) and this parity floor is its
regression guard. Parity is achieved BY CONSTRUCTION: ``Neo4jGraphStore.search`` pulls the as_of-visible
nodes out of Neo4j and delegates scoring/BFS/tie-break to a TRANSIENT in-memory ``GraphStore`` built from
those nodes — it does NOT reimplement seeding/BFS/scoring, so ids+order cannot diverge.

**No real Neo4j, no network.** A committed stdlib ``FakeBoltDriver`` / ``FakeSession`` / ``FakeTx`` behaves
like a tiny graph store for OUR known Cypher shapes and RECORDS every emitted ``(cypher, params)`` so the
tests can assert the wire shape (a node ``MERGE … SET n +=`` per write, a typed ``MERGE … [r:REL`` per
edge, a ``MATCH (n:Memory)`` on search with the ``$as_of`` bound pushed to Cypher, ``DETACH DELETE`` on
delete). ``neo4j`` is NOT installed and the offline/CI path must never import it.

**ANTI-THEATER.** Every parity case asserts the Neo4j-backed store returns the byte-identical ordered id
list as an in-memory ``GraphStore`` baseline fed the SAME writes — a silent edge mis-resolution in the
Cypher round-trip FAILS the test. One case strips the connecting ``okf_links`` edge and proves the gold
becomes unreachable from BOTH stores (recovery is traversal, not lexical). as_of is proven both ways: a
future node is excluded AND the ``$as_of`` parameter is recorded on the search ``MATCH`` (the bound was
pushed to Cypher, not only filtered in Python).

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_neo4j_parity
"""

from __future__ import annotations

import json
import unittest

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore


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
# FakeBoltDriver / FakeSession / FakeTx — a stdlib stand-in for the neo4j driver.
#
# It interprets OUR known Cypher shapes by substring (NOT a general Cypher engine):
#   * a node MERGE+`SET n +=`        -> upsert self.nodes[item_id] = props
#   * a relationship MERGE `[r:REL`  -> record only (Phase-A reads rebuild edges from okf_links)
#   * a `MATCH (n:Memory)` read       -> yield a record per node, honoring the $as_of and single-id filters
#   * a `DETACH DELETE`               -> pop the id, yield the deleted count
#   * a constraint/index call         -> no-op
# Every (cypher, merged_params) is appended to self.calls so tests can assert the wire shape.
# --------------------------------------------------------------------------- #
class _FakeRecord:
    """A neo4j-record-like row: ``record["n"]`` -> the node props dict, ``record["c"]`` -> a scalar."""

    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def __getitem__(self, key):
        return self._m[key]

    def get(self, key, default=None):
        return self._m.get(key, default)


class _FakeResult:
    """Iterable result that also supports ``.single()`` (for the delete count)."""

    def __init__(self, records: list) -> None:
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class FakeTx:
    """Transaction object: ``run(cypher, parameters=None, **kwargs)`` interpreted by substring."""

    def __init__(self, driver: "FakeBoltDriver") -> None:
        self._driver = driver

    def run(self, cypher: str, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        self._driver.calls.append((cypher, params))
        nodes = self._driver.nodes

        if "DETACH DELETE" in cypher:
            removed = nodes.pop(params["id"], None)
            return _FakeResult([_FakeRecord({"c": 1 if removed is not None else 0})])

        # Max-seq read (constructor): `MATCH (n:Memory) RETURN coalesce(max(n.seq), -1) AS m`. Check
        # BEFORE the generic node-read branch (it also has MATCH (n:Memory) but RETURNs `m`, not `n`).
        if "max(n.seq)" in cypher:
            seqs = [p.get("seq") for p in nodes.values() if p.get("seq") is not None]
            return _FakeResult([_FakeRecord({"m": max(seqs) if seqs else -1})])

        # Node upsert: `MERGE (n:Memory {item_id: $item_id}) ON CREATE SET n.seq = $seq SET n += $props`.
        # Model `ON CREATE`: a NEW id gets props + the passed seq; an EXISTING id merges props but PRESERVES
        # its original seq (so a rewrite does not reorder all()). Check before the rel-merge (which has no
        # `SET n +=`) and before the read branches.
        if "SET n +=" in cypher:
            iid = params["item_id"]
            if iid in nodes:
                preserved_seq = nodes[iid].get("seq")
                merged = dict(params["props"])
                merged["seq"] = preserved_seq  # ON CREATE only -> seq is sticky across a rewrite
                nodes[iid] = merged
            else:
                created = dict(params["props"])
                created["seq"] = params.get("seq")  # ON CREATE sets the seq for a brand-new node
                nodes[iid] = created
            return _FakeResult([])

        # Typed relationship MERGE — record only; Phase-A reads rebuild edges from okf_links on the node.
        if "[r:REL" in cypher:
            return _FakeResult([])

        # Read: a `MATCH (n:Memory …) … RETURN n`. The label may be followed by `)` (all/search) or an
        # inline id filter `{item_id: $id})` (get) — match on the label prefix so both shapes are read.
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
            return _FakeResult([_FakeRecord({"n": props}) for _, props in rows])

        # Constraint / index ensure, or anything else our store emits — no-op, already recorded.
        return _FakeResult([])


class FakeSession:
    """Context-manager session exposing ``execute_write`` / ``execute_read`` (managed transactions)."""

    def __init__(self, driver: "FakeBoltDriver") -> None:
        self._driver = driver

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def execute_write(self, fn, *args, **kwargs):
        return fn(FakeTx(self._driver), *args, **kwargs)

    def execute_read(self, fn, *args, **kwargs):
        return fn(FakeTx(self._driver), *args, **kwargs)

    def run(self, cypher: str, parameters=None, **kwargs):
        # Auto-commit path (used for the one-shot constraint ensure if the store prefers it).
        return FakeTx(self._driver).run(cypher, parameters, **kwargs)


class FakeBoltDriver:
    """A tiny in-RAM stand-in for ``neo4j.GraphDatabase.driver(...)``. NO real neo4j, NO network."""

    def __init__(self) -> None:
        self.nodes: dict = {}     # item_id -> props dict (content/timestamp/.../metadata json/seq)
        self.calls: list = []     # [(cypher, merged_params)] — every emitted statement, for shape asserts
        self.closed = False

    def session(self, database=None, **kwargs) -> FakeSession:
        return FakeSession(self)

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# Import the backend under test. EVAL-FIRST: this raises until neo4j_store exists,
# which is exactly the RED state. We import inside a helper so the import error is
# attributed to each test (clear RED) rather than to module collection.
# --------------------------------------------------------------------------- #
def _Neo4jGraphStore():
    from memeval.stores.neo4j_store import Neo4jGraphStore
    return Neo4jGraphStore


def _baseline(*, max_depth: int = 2, strip_links: bool = False) -> GraphStore:
    g = GraphStore(max_depth=max_depth)
    for it in CORPUS:
        g.write(_strip(it) if strip_links else it)
    return g


def _neo4j(*, max_depth: int = 2, strip_links: bool = False):
    Neo4jGraphStore = _Neo4jGraphStore()
    drv = FakeBoltDriver()
    s = Neo4jGraphStore(driver=drv, max_depth=max_depth)
    for it in CORPUS:
        s.write(_strip(it) if strip_links else it)
    return s, drv


def _strip(it: MemoryItem) -> MemoryItem:
    md = dict(it.metadata or {})
    md["okf_links"] = []
    return MemoryItem(item_id=it.item_id, content=it.content, timestamp=it.timestamp, metadata=md)


# --------------------------------------------------------------------------- #
# 1. Fail-loud seam (mirrors VoyageEmbedder's missing-key RuntimeError discipline)
# --------------------------------------------------------------------------- #
class FailLoudTests(unittest.TestCase):
    def test_uri_without_neo4j_raises(self) -> None:
        # neo4j is NOT installed; a set uri with no usable driver must fail loud, not silently no-op.
        Neo4jGraphStore = _Neo4jGraphStore()
        with self.assertRaises(RuntimeError):
            Neo4jGraphStore(uri="bolt://localhost:7687")

    def test_no_uri_and_no_driver_raises(self) -> None:
        # A graph-DB backend needs a uri or an injected driver — it is NOT an offline default
        # (the offline default remains the in-memory GraphStore).
        Neo4jGraphStore = _Neo4jGraphStore()
        with self.assertRaises(RuntimeError):
            Neo4jGraphStore()

    def test_no_module_load_import_of_neo4j(self) -> None:
        # The offline path must never import neo4j: importing the module must not pull it in.
        import importlib
        import sys

        sys.modules.pop("memeval.stores.neo4j_store", None)
        importlib.import_module("memeval.stores.neo4j_store")
        self.assertNotIn("neo4j", sys.modules,
                         "importing neo4j_store must NOT import neo4j (offline/CI path stays clean)")

    def test_connect_passes_uri_and_auth_to_driver(self) -> None:
        # The paid path must forward BOTH the uri AND the auth to neo4j.GraphDatabase.driver — a real
        # auth-required Neo4j is the "runs under load" target (D039); a dropped auth fails to authenticate.
        # Inject a fake `neo4j` module so connect() succeeds offline and captures what it was handed.
        import sys
        import types

        captured: dict = {}

        class _FakeGraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                captured["uri"] = uri
                captured["auth"] = auth
                return FakeBoltDriver()

        fake = types.ModuleType("neo4j")
        fake.GraphDatabase = _FakeGraphDatabase  # type: ignore[attr-defined]
        saved = sys.modules.get("neo4j")
        sys.modules["neo4j"] = fake
        try:
            Neo4jGraphStore = _Neo4jGraphStore()
            Neo4jGraphStore(uri="bolt://localhost:7687", auth=("neo4j", "secret"))
        finally:
            if saved is not None:
                sys.modules["neo4j"] = saved
            else:
                sys.modules.pop("neo4j", None)
        self.assertEqual(captured.get("uri"), "bolt://localhost:7687")
        self.assertEqual(captured.get("auth"), ("neo4j", "secret"),
                         "connect() must forward auth to the real driver (a set auth must not be dropped)")


# --------------------------------------------------------------------------- #
# 2-4. Parity across the typed/disambiguation/multi-hop/untyped slices
# --------------------------------------------------------------------------- #
class ParityTests(unittest.TestCase):
    def _assert_parity(self, query: str, *, k: int = 5, max_depth: int = 2, as_of=None) -> list:
        """The Neo4j-backed store returns the BYTE-IDENTICAL ordered id list as the in-memory baseline."""
        base = _baseline(max_depth=max_depth)
        store, _drv = _neo4j(max_depth=max_depth)
        base_ids = _ids(base.search(query, k=k, as_of=as_of))
        neo_ids = _ids(store.search(query, k=k, as_of=as_of))
        self.assertEqual(neo_ids, base_ids,
                         f"parity broken for {query!r}: neo4j={neo_ids} != baseline={base_ids}")
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
        store, drv = _neo4j()
        # mh-delta has ts=11.0; an as_of=10.5 must exclude it from BOTH stores.
        base_ids = _ids(base.search("Delta commits", k=5, as_of=10.5))
        neo_ids = _ids(store.search("Delta commits", k=5, as_of=10.5))
        self.assertEqual(neo_ids, base_ids)
        self.assertNotIn("mh-delta", neo_ids, "future node excluded by as_of in the neo4j-backed store")

    def test_as_of_pushed_to_cypher(self) -> None:
        # NO-LEAK proof: the bound is enforced in Cypher (MATCH carries $as_of), not only in Python.
        store, drv = _neo4j()
        drv.calls.clear()
        store.search("Delta commits", k=5, as_of=10.5)
        match_calls = [(c, p) for (c, p) in drv.calls if "MATCH (n:Memory)" in c and "RETURN n" in c]
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
        Neo4jGraphStore = _Neo4jGraphStore()
        base = GraphStore()
        store = Neo4jGraphStore(driver=FakeBoltDriver())
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
        Neo4jGraphStore = _Neo4jGraphStore()
        store = Neo4jGraphStore(driver=FakeBoltDriver())
        store.write(MemoryItem(item_id="x1", content="payload", timestamp=7.5, relevancy=0.3,
                               session_id="sess-9", source="unit", tags=["a", "b"], tokens=42,
                               version=4, metadata={"okf_title": "x1", "k": "v"}))
        got = store.get("x1")
        self.assertEqual(
            (got.content, got.timestamp, got.relevancy, got.session_id, got.source,
             got.tags, got.tokens, got.version, got.metadata.get("k")),
            ("payload", 7.5, 0.3, "sess-9", "unit", ["a", "b"], 42, 4, "v"),
            "every PERSISTED MemoryItem field round-trips through the Neo4j props "
            "(embedding is excluded BY DESIGN — recomputed from content like SqliteVectorStore/GraphStore)")

    def test_post_close_write_and_delete_fail_loud(self) -> None:
        Neo4jGraphStore = _Neo4jGraphStore()
        store = Neo4jGraphStore(driver=FakeBoltDriver())
        store.write(_item("a", "a", ts=1.0))
        store.close()
        with self.assertRaises(RuntimeError):
            store.write(_item("b", "b", ts=2.0))
        with self.assertRaises(RuntimeError):
            store.delete("a")
        # Reads hit Neo4j live (no in-RAM cache) -> a post-close read must FAIL LOUD too, not deref a
        # nulled driver with an AttributeError.
        with self.assertRaises(RuntimeError):
            store.search("a", k=1)
        with self.assertRaises(RuntimeError):
            store.get("a")
        with self.assertRaises(RuntimeError):
            store.all()

    def test_context_manager_closes_driver(self) -> None:
        Neo4jGraphStore = _Neo4jGraphStore()
        drv = FakeBoltDriver()
        with Neo4jGraphStore(driver=drv) as store:
            store.write(_item("a", "a", ts=1.0))
        self.assertTrue(drv.closed, "__exit__ closed the driver")

    def test_tokens_estimated_when_zero_parity(self) -> None:
        # An item written with tokens=0 + content gets an ESTIMATE persisted (mirrors GraphStore.write),
        # so get()/all() tokens match the in-memory baseline instead of round-tripping a bare 0.
        Neo4jGraphStore = _Neo4jGraphStore()
        base = GraphStore()
        store = Neo4jGraphStore(driver=FakeBoltDriver())
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
        Neo4jGraphStore = _Neo4jGraphStore()
        base = GraphStore()
        store = Neo4jGraphStore(driver=FakeBoltDriver())
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
        # Two stores sharing ONE driver model a restart/2nd-instance over the SAME DB: store1 writes a,b;
        # store2 (re-init over the same driver) writes c. all() order must be (a,b,c) — store2 must derive
        # its starting seq from the DB max, not from 0 (which would collide a's/b's seq and scramble order).
        Neo4jGraphStore = _Neo4jGraphStore()
        drv = FakeBoltDriver()
        store1 = Neo4jGraphStore(driver=drv)
        store1.write(_item("a", "a", ts=1.0))
        store1.write(_item("b", "b", ts=2.0))
        store2 = Neo4jGraphStore(driver=drv)  # "restart" over the same DB
        store2.write(_item("c", "c", ts=3.0))
        self.assertEqual([i.item_id for i in store2.all()], ["a", "b", "c"],
                         "store2 continued the seq from the DB max -> insertion order survives restart")


# --------------------------------------------------------------------------- #
# 6c. Constructor robustness — a failed constraint must not strand a driver the constructor OWNS;
#     _row_to_item tolerates native (non-JSON-string) props a future Phase-B/external writer may emit.
# --------------------------------------------------------------------------- #
class _RaisingConstraintDriver(FakeBoltDriver):
    """A driver whose constraint write RAISES — to prove the constructor cleans up after itself."""

    def session(self, database=None, **kwargs):
        return _RaisingConstraintSession(self)


class _RaisingConstraintSession(FakeSession):
    def execute_write(self, fn, *args, **kwargs):
        # Only the constraint ensure goes through execute_write at construction time; make it raise.
        raise RuntimeError("constraint creation failed (simulated)")


class ConstructorRobustnessTests(unittest.TestCase):
    def test_constraint_failure_closes_owned_driver(self) -> None:
        # uri path: the constructor BUILT the driver, so a failed constraint must close it before
        # re-raising — a failed __init__ must not strand a connection pool.
        import sys
        import types

        built = {}

        class _FakeGraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                d = _RaisingConstraintDriver()
                built["driver"] = d
                return d

        fake = types.ModuleType("neo4j")
        fake.GraphDatabase = _FakeGraphDatabase  # type: ignore[attr-defined]
        saved = sys.modules.get("neo4j")
        sys.modules["neo4j"] = fake
        try:
            Neo4jGraphStore = _Neo4jGraphStore()
            with self.assertRaises(RuntimeError):
                Neo4jGraphStore(uri="bolt://localhost:7687")
        finally:
            if saved is not None:
                sys.modules["neo4j"] = saved
            else:
                sys.modules.pop("neo4j", None)
        self.assertTrue(built["driver"].closed,
                        "a constructor-owned driver must be closed when the constraint ensure fails")

    def test_constraint_failure_does_not_close_injected_driver(self) -> None:
        # injected path: the CALLER owns the driver, so the constructor must NOT close it on failure.
        Neo4jGraphStore = _Neo4jGraphStore()
        drv = _RaisingConstraintDriver()
        with self.assertRaises(RuntimeError):
            Neo4jGraphStore(driver=drv)
        self.assertFalse(drv.closed,
                         "an injected (caller-owned) driver must NOT be closed by a failed __init__")

    def test_row_to_item_tolerates_native_props(self) -> None:
        # Phase-B-proofing: a future external/Phase-B writer might store tags as a native list and metadata
        # as a native map (Neo4j supports lists-of-scalars natively). _row_to_item must not crash on those.
        Neo4jGraphStore = _Neo4jGraphStore()
        store = Neo4jGraphStore(driver=FakeBoltDriver())
        item = store._row_to_item({
            "item_id": "nat", "content": "c", "timestamp": 1.0, "relevancy": 0.5,
            "session_id": "s", "source": "src", "tags": ["x", "y"], "tokens": 3,
            "version": 2, "metadata": {"okf_title": "nat", "k": "v"},  # NATIVE list + dict, not JSON strings
        })
        self.assertEqual(item.tags, ["x", "y"])
        self.assertEqual(item.metadata.get("k"), "v")


# --------------------------------------------------------------------------- #
# 7. Cypher-shape assertions — the wire shape is the real Neo4j graph (Phase-B substrate)
# --------------------------------------------------------------------------- #
class CypherShapeTests(unittest.TestCase):
    def test_write_emits_node_merge_and_typed_rel_merge(self) -> None:
        Neo4jGraphStore = _Neo4jGraphStore()
        drv = FakeBoltDriver()
        store = Neo4jGraphStore(driver=drv)
        drv.calls.clear()
        store.write(_item("rd-hub", "Hub coordinates flows.",
                          [["depends on", "rd-alpha"], ["conflicts with", "rd-beta"]], ts=1.0))
        cyphers = [c for (c, _p) in drv.calls]
        self.assertTrue(any("MERGE" in c and "SET n +=" in c for c in cyphers),
                        "a node MERGE … SET n += per write")
        rel_calls = [(c, p) for (c, p) in drv.calls if "[r:REL" in c]
        self.assertEqual(len(rel_calls), 2, "one typed-relationship MERGE per parsed okf_links edge")
        rels = {p.get("rel") for (_c, p) in rel_calls}
        self.assertEqual(rels, {"depends_on", "conflicts_with"},
                         "the edge rel_type is the CLASSIFIED relation (anchor -> closed enum)")
        # the node props carry the metadata (incl. okf_links) as a JSON string — the SSOT for reads
        merge_calls = [p for (c, p) in drv.calls if "SET n +=" in c]
        props = merge_calls[0]["props"]
        self.assertIn("metadata", props)
        self.assertIn("okf_links", json.loads(props["metadata"]))

    def test_search_emits_match_and_delete_emits_detach_delete(self) -> None:
        Neo4jGraphStore = _Neo4jGraphStore()
        drv = FakeBoltDriver()
        store = Neo4jGraphStore(driver=drv)
        store.write(_item("a", "a", ts=1.0))
        drv.calls.clear()
        store.search("a", k=1)
        self.assertTrue(any("MATCH (n:Memory)" in c and "RETURN n" in c for (c, _p) in drv.calls),
                        "search emits a MATCH (n:Memory) … RETURN n read")
        drv.calls.clear()
        store.delete("a")
        self.assertTrue(any("DETACH DELETE" in c for (c, _p) in drv.calls),
                        "delete emits DETACH DELETE")


# --------------------------------------------------------------------------- #
# 8. ANTI-THEATER — id-set/order is the contract
# --------------------------------------------------------------------------- #
class AntiTheaterTests(unittest.TestCase):
    def test_stripping_edge_makes_gold_unreachable_in_both(self) -> None:
        # If the connecting okf_links edge is stripped, the lexically-inert gold is unreachable from BOTH
        # stores -> recovery is TRAVERSAL, not lexical. (Apex->Bravo: Bravo is reachable only via the edge.)
        base_links = _baseline()
        store_links, _d1 = _neo4j()
        self.assertIn("mh-bravo", _ids(store_links.search("Apex chain", k=5)),
                      "with the edge, Bravo is reached via traversal in the neo4j-backed store")
        self.assertIn("mh-bravo", _ids(base_links.search("Apex chain", k=5)))

        base_nolinks = _baseline(strip_links=True)
        store_nolinks, _d2 = _neo4j(strip_links=True)
        self.assertNotIn("mh-bravo", _ids(store_nolinks.search("Apex chain", k=5)),
                         "strip the edge -> Bravo unreachable from the neo4j-backed store (not lexical)")
        self.assertNotIn("mh-bravo", _ids(base_nolinks.search("Apex chain", k=5)),
                         "strip the edge -> Bravo unreachable from the baseline too (parity holds)")

    def test_byte_identical_ordered_ids_across_full_corpus(self) -> None:
        # The contract: a multi-item ordered id list is byte-identical between the two stores. A silent
        # edge mis-resolution in the Cypher round-trip would reorder/drop an id and FAIL here.
        base = _baseline(max_depth=3)
        store, _drv = _neo4j(max_depth=3)
        for query in ("Zephyr dependency", "Zephyr dependents", "Hub conflict", "Hub dependency",
                      "Hub callee", "Apex chain tail", "Solis related", "Nimbus related",
                      "Quasar partitions", "Delta commits writes"):
            base_ids = _ids(base.search(query, k=5))
            neo_ids = _ids(store.search(query, k=5))
            self.assertEqual(neo_ids, base_ids,
                             f"byte-identical ordered ids required for {query!r}: "
                             f"neo4j={neo_ids} != baseline={base_ids}")


if __name__ == "__main__":
    unittest.main()
