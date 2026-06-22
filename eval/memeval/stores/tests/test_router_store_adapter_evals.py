"""RouterStore adapter eval — make write-routing LIVE. Owner: Brent (@bgibson1618).

``Router`` owns WHERE to store (``route_write`` / ``write``, D009/D023/D024) and WHERE to read
(``route``), but it is NOT a :class:`memeval.protocols.MemoryStore`: ``Router.write`` returns a
``WriteReceipt`` (not ``None``), ``route`` returns a *store* (not results), and it has no ``get`` /
``all``. So nothing that takes a ``MemoryStore`` (the plugin ``_Engine``, the harness
``MemoryFramework``, and the #63 native eval pipeline's ``store=`` seam) can use the Router's routed,
multi-index, dedup-aware write path. Routed write-routing/dedup are therefore BUILT but NOT LIVE —
every production write goes direct to a single backend.

``RouterStore`` is the thin adapter that closes that gap: a :class:`MemoryStore` facade whose
``write(item)`` calls :meth:`Router.write` (dedup -> route_write -> fan to every policy backend) and
whose ``get`` / ``search`` / ``all`` delegate through the Router. It is the exact glue the two stub
call sites (``plugin/.../client.py`` ``_Engine.remember``; ``eval/memeval/opencode/framework.py``
``MemoryFramework.write/get/search/all``) will adopt, AND it lets Brent inject routed writes into the
#63 native harness (``run_native(store=...)`` / ``BaseNativeEvaluator.run_tasks(store=...)``) TODAY,
solo — the first end-to-end run that exercises his real stores through write-routing.

What this eval asserts (all OFFLINE + deterministic — rule classifier + stdlib hashing embedder +
in-memory graph; the MarkdownStore base needs a real dir so it uses a tempdir, hence a read-only
sandbox with no writable tmp cannot run these — environmental, not a code failure):

* protocol conformance — ``RouterStore`` satisfies ``MemoryStore`` (so it drops into any store seam);
* fan-out (anti-theater) — one ``write`` lands the item in ALL backends under ``base_all``, proving
  routed multi-index writes actually happen (not single-backend like the bypassed path today);
* facade round-trip — write via the adapter, then ``search`` its matching query returns it (the
  base_all recall guarantee, D023, now through the unified facade);
* cross-backend read-dedup — ``all()`` returns each item ONCE despite the fan-out copies;
* passthrough — ``search`` forwards ``k`` and ``as_of`` (no-future-peeking preserved);
* the dedup knob — a Router configured ``dedup=True`` merges a verbatim duplicate (mechanism;
  ``dedup`` stays OFF by default per D024 — offline lexical similarity can't separate near-dups);
* native integration — ``RouterStore`` drives the #63 native run-path (``run_tasks(store=...)``):
  every session write fans through the Router to all three backends, proving routed writes are LIVE
  inside the benchmark pipeline.

Provenance: the (memory, matching-query) cases are a subset of the D023 write-routing corpus already
calibrated to round-trip under ``base_all`` (verified against the real stores, not asserted by an LLM).
See DECISION_LOG D023/D024 + the next entry for the adapter.

Reproduce:    cd eval && python3 -m memeval.stores.tests.test_router_store_adapter_evals
Run the guard: cd eval && python3 -m unittest memeval.stores.tests.test_router_store_adapter_evals
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from dataclasses import dataclass

from memeval.protocols import MemoryStore
from memeval.router import GRAPH, MARKDOWN, VECTORS, Router, RouterConfig, RouterStore
from memeval.schema import Benchmark, MemoryItem, Session, Task, TaskKind
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore

K = 5


@dataclass(frozen=True)
class Case:
    """One (memory, matching-query) round-trip case, drawn from the D023-calibrated corpus."""

    item_id: str
    content: str
    matching_query: str
    links: tuple = ()


# A small intent-spanning slice (markdown literal / graph relational / vectors rationale) of the
# D023 corpus, each already proven to round-trip under base_all against the real stores.
CASES = (
    Case(
        item_id="lit-session-token-ttl",
        content="The authentication service issues session tokens with a TTL of 1800 seconds, after which the client must refresh.",
        matching_query="what is our session token TTL?",
    ),
    Case(
        item_id="lit-rate-limit-threshold",
        content="The public API gateway enforces a rate limit of 100 requests per minute per API key, returning HTTP 429 when exceeded.",
        matching_query="what is the rate limit on the public API per API key?",
    ),
    Case(
        item_id="rel-billing-worker-depends-on-gateway-ledger",
        content="The billing worker depends on the Stripe payment gateway for charge execution and on the ledger database for recording settled transactions.",
        matching_query="what does the billing worker depend on?",
        links=("rel-payment-gateway-calls-fraud-service",),
    ),
    Case(
        item_id="rel-notification-conflicts-with-legacy-mailer",
        content="The new notification service conflicts with the legacy mailer cron job because both subscribe to the same order-confirmed queue and would send duplicate emails if run together.",
        matching_query="what conflicts with the legacy mailer cron job?",
    ),
    Case(
        item_id="vec-cart-optimistic-locking-rationale",
        content="We chose optimistic locking for the shopping cart over pessimistic row locks because flash-sale traffic caused heavy lock contention, and most concurrent edits touch different line items so conflicts are rare and cheap to retry.",
        matching_query="why did we go with optimistic locking for the cart instead of locking the rows?",
    ),
    Case(
        item_id="vec-sessions-redis-over-jwt-rationale",
        content="We decided to keep user sessions in Redis rather than use stateless JWTs because we needed instant revocation on logout and password change, which signed tokens cannot deliver without a separate blocklist that erases their statelessness benefit.",
        matching_query="what was the reasoning for storing sessions in Redis rather than using self-contained tokens?",
    ),
)

_EXPECTED_CASES = 6  # size lock — changing the corpus is deliberate


def _mk_item(c: "Case", **overrides) -> MemoryItem:
    meta: dict = {"okf_title": c.item_id}
    if c.links:
        meta["okf_links"] = list(c.links)
    fields = dict(item_id=c.item_id, content=c.content, metadata=meta)
    fields.update(overrides)
    return MemoryItem(**fields)


class _RecordingStore:
    """A minimal MemoryStore that records the kwargs of its last ``search`` (kwargs-passthrough probe)."""

    def __init__(self) -> None:
        self.last_search_kwargs = None

    def write(self, item: MemoryItem) -> None:  # pragma: no cover - not exercised here
        pass

    def get(self, item_id: str):  # -> Optional[MemoryItem]
        return None

    def search(self, query: str, *, k: int = 5, as_of=None, **kwargs):
        self.last_search_kwargs = kwargs
        return []

    def all(self) -> list:
        return []


@contextlib.contextmanager
def _router_store(tmp: str, **config_kwargs):
    """A RouterStore over the three real backends (markdown in ``tmp``); closes sqlite on exit."""
    backends = {
        MARKDOWN: MarkdownStore(os.path.join(tmp, "md")),
        VECTORS: SqliteVectorStore(),
        GRAPH: GraphStore(),
    }
    router = Router.with_config(backends=backends, config=RouterConfig(**config_kwargs))
    try:
        yield RouterStore(router), backends
    finally:
        backends[VECTORS].close()


class RouterStoreContractTests(unittest.TestCase):
    def test_fixture_size_locked(self) -> None:
        self.assertEqual(len(CASES), _EXPECTED_CASES, "case count changed — update _EXPECTED_CASES")
        ids = [c.item_id for c in CASES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate case item_id")

    def test_satisfies_memorystore_protocol(self) -> None:
        # The whole point: the adapter drops into any seam typed as MemoryStore.
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                self.assertIsInstance(rs, MemoryStore)

    def test_write_fans_out_to_all_backends_base_all(self) -> None:
        # Anti-theater: a single adapter write must land the item in EVERY backend under the
        # base_all default — the routed multi-index write the bypassed markdown-only path never did.
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, backends):
                item = _mk_item(CASES[0])
                rs.write(item)
                for name in (MARKDOWN, VECTORS, GRAPH):
                    self.assertIsNotNone(
                        backends[name].get(item.item_id),
                        f"base_all write did not reach the {name} backend",
                    )

    def test_facade_round_trips_every_case(self) -> None:
        # The accuracy guarantee through the unified facade: write via the adapter, then search the
        # matching query via the adapter — every case comes back (base_all, D023).
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                for c in CASES:
                    rs.write(_mk_item(c))
                misses = []
                for c in CASES:
                    ids = {r.item_id for r in rs.search(c.matching_query, k=K)}
                    if c.item_id not in ids:
                        misses.append(c.item_id)
                self.assertEqual(misses, [], f"adapter round-trip missed: {misses}")

    def test_search_returns_retrieved_items_ranked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                for c in CASES:
                    rs.write(_mk_item(c))
                hits = rs.search(CASES[0].matching_query, k=K)
                self.assertTrue(hits, "expected at least one hit")
                self.assertEqual([h.rank for h in hits], sorted(h.rank for h in hits))
                self.assertEqual(hits[0].rank, 0, "best hit must be rank 0")

    def test_get_returns_written_item_and_none_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                rs.write(_mk_item(CASES[0]))
                got = rs.get(CASES[0].item_id)
                self.assertIsNotNone(got)
                self.assertEqual(got.item_id, CASES[0].item_id)
                self.assertIsNone(rs.get("no-such-id"))

    def test_all_dedups_the_fan_out_copies(self) -> None:
        # base_all writes each item to 3 backends; all() must return each logical item ONCE.
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                for c in CASES:
                    rs.write(_mk_item(c))
                ids = [m.item_id for m in rs.all()]
                self.assertEqual(len(ids), len(set(ids)), "all() returned duplicate item_ids")
                self.assertEqual(set(ids), {c.item_id for c in CASES})

    def test_search_forwards_as_of(self) -> None:
        # No peeking at the future must survive the delegation.
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                c = CASES[0]
                rs.write(_mk_item(c, timestamp=100.0))
                before = {r.item_id for r in rs.search(c.matching_query, k=K, as_of=50.0)}
                after = {r.item_id for r in rs.search(c.matching_query, k=K, as_of=150.0)}
                self.assertNotIn(c.item_id, before, "as_of must hide a future item")
                self.assertIn(c.item_id, after, "as_of must admit a past item")

    def test_search_forwards_k(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):
                for c in CASES:
                    rs.write(_mk_item(c))
                self.assertLessEqual(len(rs.search(CASES[0].matching_query, k=1)), 1)

    def test_search_forwards_kwargs_through_cascade(self) -> None:
        # A routed read through a cascade-enabled profile must still honor backend kwargs (the
        # MemoryStore.search seam): RouterStore -> Router.route -> _GraphVectorCascade -> graph/vector.
        from memeval.router import CascadeConfig

        graph_rec, vec_rec = _RecordingStore(), _RecordingStore()
        backends = {GRAPH: graph_rec, VECTORS: vec_rec, MARKDOWN: _RecordingStore()}
        rs = RouterStore(Router.with_config(
            backends=backends, config=RouterConfig(cascade=CascadeConfig(enabled=True))))
        # "what depends on …" classifies GRAPH -> the cascade engages; empty graph hits fall through
        # to the vector store, so BOTH underlying searches run and must see the kwarg.
        rs.search("what depends on the billing worker?", k=5, tenant="acme")
        self.assertEqual(graph_rec.last_search_kwargs, {"tenant": "acme"},
                         "cascade dropped kwargs to the graph backend")
        self.assertEqual(vec_rec.last_search_kwargs, {"tenant": "acme"},
                         "cascade dropped kwargs to the vector fall-through backend")


class RouterStoreDedupKnobTests(unittest.TestCase):
    """The dedup knob flows through the adapter. dedup stays OFF by default (D024); a verbatim
    duplicate is a safe mechanism probe (cosine 1.0 >> threshold), sidestepping the near-dup
    ambiguity D024 measured."""

    def test_dedup_off_keeps_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, _backends):  # default dedup=False
                rs.write(_mk_item(CASES[0]))  # id A
                rs.write(_mk_item(CASES[0], item_id="dup-of-A"))  # verbatim content, new id
                self.assertIsNotNone(rs.get("dup-of-A"), "dedup OFF must keep the second write")
                ids = {m.item_id for m in rs.all()}
                self.assertIn(CASES[0].item_id, ids)
                self.assertIn("dup-of-A", ids)

    def test_dedup_on_merges_verbatim_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp, dedup=True) as (rs, _backends):
                rs.write(_mk_item(CASES[0]))  # id A, version 1
                rs.write(_mk_item(CASES[0], item_id="dup-of-A"))  # verbatim -> should MERGE into A
                self.assertIsNone(rs.get("dup-of-A"), "dedup ON must merge the verbatim duplicate")
                survivor = rs.get(CASES[0].item_id)
                self.assertIsNotNone(survivor)
                self.assertEqual(survivor.version, 2, "merge bumps version (newer content wins)")
                ids = [m.item_id for m in rs.all()]
                self.assertEqual(ids.count(CASES[0].item_id), 1)
                self.assertNotIn("dup-of-A", ids)


class RouterStoreNativeIntegrationTests(unittest.TestCase):
    """Drive RouterStore through the #63 native run-path: routed writes LIVE in the benchmark pipeline."""

    def _tasks(self) -> list[Task]:
        tasks = []
        for i, c in enumerate(CASES):
            tasks.append(
                Task(
                    task_id=f"t{i}",
                    benchmark=Benchmark.MEMORY_AGENT_BENCH,
                    kind=TaskKind.QA,
                    question=c.matching_query,
                    answer=c.item_id,
                    sessions=[Session(session_id=c.item_id, content=c.content, timestamp=10.0)],
                    gold_memory_ids=[c.item_id],
                )
            )
        return tasks

    def test_router_store_drives_native_run_path(self) -> None:
        from memeval.native.evaluators.base import BaseNativeEvaluator

        with tempfile.TemporaryDirectory() as tmp:
            with _router_store(tmp) as (rs, backends):
                ev = BaseNativeEvaluator()
                records = ev.run_tasks(self._tasks(), store=rs, memory=True, k=K)
                self.assertEqual(len(records), len(CASES), "one record per task")
                # The load-bearing claim: every write the native pipeline made (the per-session
                # memories, plus any the agent wrote back) fanned through the Router to ALL three
                # backends — routed write-routing is LIVE inside the benchmark pipeline. The robust
                # invariant is fan-out CONSISTENCY: all three backends hold the same set of ids, and
                # at least the per-session writes landed. (Count may exceed len(CASES) when the agent
                # writes derived memories — that only confirms more routed writes, not fewer.)
                counts = {name: len(backends[name].all()) for name in (MARKDOWN, VECTORS, GRAPH)}
                self.assertEqual(
                    len(set(counts.values())), 1,
                    f"routed writes did not fan out evenly across backends: {counts}",
                )
                self.assertGreaterEqual(
                    counts[MARKDOWN], len(CASES),
                    f"native run did not route the per-session writes through the Router: {counts}",
                )
                session_ids = {c.item_id for c in CASES}
                for name in (MARKDOWN, VECTORS, GRAPH):
                    have = {m.item_id for m in backends[name].all()}
                    self.assertTrue(
                        session_ids <= have,
                        f"the {name} backend is missing routed session writes: {session_ids - have}",
                    )


def _report() -> None:
    print(f"RouterStore adapter eval — {len(CASES)} cases (k={K}).\n")
    with tempfile.TemporaryDirectory() as tmp:
        with _router_store(tmp) as (rs, backends):
            for c in CASES:
                rs.write(_mk_item(c))
            hits = sum(
                1 for c in CASES if c.item_id in {r.item_id for r in rs.search(c.matching_query, k=K)}
            )
            print(f"facade round-trip recall : {hits}/{len(CASES)} = {hits / len(CASES):.3f}")
            print(f"all() unique items       : {len({m.item_id for m in rs.all()})} (no fan-out dupes)")
            print(f"backend item counts      : " + ", ".join(
                f"{n}={len(backends[n].all())}" for n in (MARKDOWN, VECTORS, GRAPH)))
    print("\nRouterStore makes the Router's routed, multi-index write path usable wherever a "
          "MemoryStore is expected (plugin remember / MemoryFramework / #63 native store=). "
          "base_all fan-out + facade round-trip; dedup OFF by default (D024). Offline + deterministic.")


if __name__ == "__main__":
    _report()
