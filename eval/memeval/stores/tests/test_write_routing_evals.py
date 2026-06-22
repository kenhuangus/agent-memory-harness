"""Write-routing round-trip eval (D023) — "accurate on writes AND retrievals". Owner: Brent.

The router owns WHERE to STORE, not just where to read (D009). This fixture measures the only metric
that captures "accurate on writes and retrievals" end-to-end: the **write→retrieve round-trip** — write
a memory via ``Router.route_write`` under a write policy, then route its matching query via
``Router.route`` and check the memory comes back.

It is fully OFFLINE + deterministic (rule classifier + stdlib hashing embedder + in-memory graph), so it
is a committed CI eval — no captained run. (The MarkdownStore base needs a real directory, so the
round-trip uses a tempdir; a read-only sandbox without a writable tmp cannot run those tests — that is
environmental, not a code failure.)

Finding it encodes (D023): selective write-placement (``base_selective``: markdown base + the
classify(content) backend) does NOT preserve round-trip recall, because content-classification and
query-classification DIVERGE under the rule classifier — a memory placed where its content classifies
is missed by a matching query that classifies elsewhere. Only writing every index (``base_all``) makes
the memory retrievable whatever backend its query routes to. So the recall-safe default is ``base_all``;
selective is a cost-saving option that only pays off with an *aligned* (learned/semantic) classifier.
The tests assert ``base_all`` round-trips EVERY pair (1.0) and that ``base_selective`` measurably loses
recall (anti-theater: the policy choice is real, and full recall requires all indexes).

Scope: this is a deterministic RULE-ROUTER corpus (lexical/domain-term overlap). It validates the
write→retrieve ROUND-TRIP (routing + store presence) and the ``base_all`` default for *this* router —
NOT broad semantic-retrieval quality (that is the embedder's eval, D019/D020). The D023 conclusion is
"selective placement doesn't pay off with the rule classifier"; an aligned/learned classifier could
revisit it.

Provenance: the (memory, matching-query) pairs were blind-generated (firewalled multi-lens workflow,
generic/non-project, 3 intents) and the policy default was chosen by a deterministic round-trip
calibration — not hand-tuned. See DECISION_LOG D023.

Reproduce:    cd eval && python3 -m memeval.stores.tests.test_write_routing_evals
Run the guard: cd eval && python3 -m unittest memeval.stores.tests.test_write_routing_evals
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass, field

from memeval.router import GRAPH, MARKDOWN, VECTORS, Router, RouterConfig
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore

K = 5


@dataclass(frozen=True)
class Pair:
    """One (memory, matching-query) round-trip case. ``query_intent`` is the generator's intent label
    (markdown|graph|vectors); the actual route is decided by the rule classifier at run time."""

    item_id: str
    query_intent: str
    title: str
    content: str
    matching_query: str
    links: tuple = ()


PAIRS = (
    Pair(
        item_id='lit-session-token-ttl', query_intent='markdown',
        title='Session token TTL',
        content='The authentication service issues session tokens with a TTL of 1800 seconds, after which the client must refresh.',
        matching_query='what is our session token TTL?',
    ),
    Pair(
        item_id='lit-checkout-retry-policy', query_intent='markdown',
        title='Checkout charge retry count',
        content='The checkout service retries a failed charge exactly 3 times using exponential backoff starting at 500ms.',
        matching_query='how many times does checkout retry a failed charge?',
    ),
    Pair(
        item_id='lit-redis-cache-ttl', query_intent='markdown',
        title='Product cache TTL in Redis',
        content='Product detail responses are cached in Redis under the key prefix prod: with a TTL of 600 seconds.',
        matching_query='what TTL do we use for the product cache in Redis?',
    ),
    Pair(
        item_id='lit-postgres-pool-max', query_intent='markdown',
        title='Postgres connection pool max size',
        content='The Postgres connection pool is configured with a maximum of 20 connections per service instance.',
        matching_query='what is the max Postgres connection pool size?',
    ),
    Pair(
        item_id='lit-rate-limit-threshold', query_intent='markdown',
        title='Public API rate limit',
        content='The public API gateway enforces a rate limit of 100 requests per minute per API key, returning HTTP 429 when exceeded.',
        matching_query='what is the rate limit on the public API per API key?',
    ),
    Pair(
        item_id='lit-kafka-consumer-group', query_intent='markdown',
        title='Order events consumer group ID',
        content='The order-fulfillment workers consume from the orders.created topic using the consumer group ID fulfillment-svc-prod.',
        matching_query='what consumer group ID does order fulfillment use for orders.created?',
    ),
    Pair(
        item_id='lit-jwt-signing-alg', query_intent='markdown',
        title='JWT signing algorithm',
        content='Access tokens are signed with the RS256 algorithm using the key ID auth-key-2024-q3.',
        matching_query='which algorithm do we sign JWTs with?',
    ),
    Pair(
        item_id='lit-log-retention-days', query_intent='markdown',
        title='Application log retention period',
        content='Application logs in the centralized logging cluster are retained for 30 days before automatic deletion.',
        matching_query='how long are application logs retained?',
    ),
    Pair(
        item_id='rel-billing-worker-depends-on-gateway-ledger', query_intent='graph',
        title='Billing worker dependencies',
        content='The billing worker depends on the Stripe payment gateway for charge execution and on the ledger database for recording settled transactions.',
        matching_query='what does the billing worker depend on?',
        links=('rel-payment-gateway-calls-fraud-service', 'rel-ledger-db-replicates-to-warehouse'),
    ),
    Pair(
        item_id='rel-payment-gateway-calls-fraud-service', query_intent='graph',
        title='Payment gateway calls fraud service',
        content='The payment gateway calls the fraud-scoring service synchronously before authorizing any charge, and declines the charge if the score exceeds the risk threshold.',
        matching_query='which service does the payment gateway call before authorizing a charge?',
        links=('rel-billing-worker-depends-on-gateway-ledger',),
    ),
    Pair(
        item_id='rel-ledger-db-replicates-to-warehouse', query_intent='graph',
        title='Ledger DB replicates to warehouse',
        content='The ledger database replicates its committed transactions to the analytics warehouse through a nightly change-data-capture pipeline.',
        matching_query='where does the ledger database send its data downstream?',
        links=('rel-billing-worker-depends-on-gateway-ledger',),
    ),
    Pair(
        item_id='rel-auth-service-uses-redis-sessions', query_intent='graph',
        title='Auth service uses Redis for sessions',
        content='The authentication service uses Redis as the backing store for session tokens and rate-limit counters, falling back to read-only mode if Redis is unreachable.',
        matching_query='what backing store does the auth service use for sessions?',
        links=('rel-api-gateway-depends-on-auth-service',),
    ),
    Pair(
        item_id='rel-api-gateway-depends-on-auth-service', query_intent='graph',
        title='API gateway depends on auth service',
        content='The API gateway depends on the authentication service to validate bearer tokens on every inbound request and rejects traffic when token validation fails.',
        matching_query='what does the API gateway depend on to validate requests?',
        links=('rel-auth-service-uses-redis-sessions',),
    ),
    Pair(
        item_id='rel-search-indexer-consumes-product-events', query_intent='graph',
        title='Search indexer consumes product events',
        content='The search indexer consumes product-update events from the Kafka catalog topic and writes the transformed documents into the Elasticsearch cluster.',
        matching_query='where does the search indexer get its data from and where does it write?',
        links=('rel-catalog-service-publishes-to-kafka',),
    ),
    Pair(
        item_id='rel-catalog-service-publishes-to-kafka', query_intent='graph',
        title='Catalog service publishes to Kafka',
        content="The catalog service publishes product-update events to the Kafka catalog topic whenever a product's price or inventory changes.",
        matching_query='which topic does the catalog service publish product changes to?',
        links=('rel-search-indexer-consumes-product-events',),
    ),
    Pair(
        item_id='rel-notification-conflicts-with-legacy-mailer', query_intent='graph',
        title='Notification service conflicts with legacy mailer',
        content='The new notification service conflicts with the legacy mailer cron job because both subscribe to the same order-confirmed queue and would send duplicate emails if run together.',
        matching_query='what conflicts with the legacy mailer cron job?',
    ),
    Pair(
        item_id='vec-cart-optimistic-locking-rationale', query_intent='vectors',
        title='Optimistic locking chosen for shopping cart',
        content='We chose optimistic locking for the shopping cart over pessimistic row locks because flash-sale traffic caused heavy lock contention, and most concurrent edits touch different line items so conflicts are rare and cheap to retry.',
        matching_query='why did we go with optimistic locking for the cart instead of locking the rows?',
    ),
    Pair(
        item_id='vec-sessions-redis-over-jwt-rationale', query_intent='vectors',
        title='Server-side Redis sessions chosen over stateless JWTs',
        content='We decided to keep user sessions in Redis rather than use stateless JWTs because we needed instant revocation on logout and password change, which signed tokens cannot deliver without a separate blocklist that erases their statelessness benefit.',
        matching_query='what was the reasoning for storing sessions in Redis rather than using self-contained tokens?',
    ),
    Pair(
        item_id='vec-search-elasticsearch-vs-postgres-rationale', query_intent='vectors',
        title='Elasticsearch adopted for product search',
        content='We moved product search off Postgres full-text and onto Elasticsearch because we needed typo tolerance, relevance tuning, and faceted filtering at scale, and bolting those onto SQL queries was becoming unmaintainable and slow.',
        matching_query='why did we pick a dedicated search engine over database full-text search for products?',
    ),
    Pair(
        item_id='vec-payments-idempotency-keys-rationale', query_intent='vectors',
        title='Idempotency keys required on payment endpoint',
        content='We require clients to send an idempotency key on the charge endpoint because network retries and double-clicks were producing duplicate charges, and the key lets us safely return the original result instead of billing the customer twice.',
        matching_query="what's the rationale behind requiring idempotency keys when charging a customer?",
    ),
    Pair(
        item_id='vec-deploy-blue-green-rationale', query_intent='vectors',
        title='Blue-green deployment strategy adopted',
        content='We adopted blue-green deployments instead of rolling updates because we wanted instant rollback by flipping the load balancer and a fully warmed environment before cutover, accepting the higher infrastructure cost in exchange for near-zero-risk releases.',
        matching_query='why did the team choose blue-green releases over a rolling deploy?',
    ),
    Pair(
        item_id='vec-events-at-least-once-rationale', query_intent='vectors',
        title='At-least-once delivery chosen for the event bus',
        content='We chose at-least-once delivery with idempotent consumers for the event bus rather than exactly-once because true exactly-once is prohibitively complex and slow across distributed brokers, and making consumers idempotent gives equivalent correctness more cheaply.',
        matching_query='why did we settle on at-least-once messaging instead of exactly-once delivery?',
    ),
    Pair(
        item_id='vec-logging-structured-json-rationale', query_intent='vectors',
        title='Structured JSON logging mandated',
        content='We standardized on structured JSON logs with correlation IDs instead of free-form text because we needed to trace a single request across services and query logs programmatically, which plain string logs made painfully unreliable.',
        matching_query='what was the motivation for switching all services to structured JSON logging?',
    ),
    Pair(
        item_id='vec-db-read-replicas-rationale', query_intent='vectors',
        title='Read replicas added for reporting queries',
        content='We routed analytics and reporting queries to read replicas because heavy aggregate scans were degrading latency on the primary write path, and isolating read load protected transactional performance during business-hours reporting.',
        matching_query='why did we offload reporting traffic to read replicas?',
    ),
)


_EXPECTED_PAIRS = 24  # hardcoded size lock — changing the fixture is deliberate


def _mk_item(p: "Pair") -> MemoryItem:
    meta: dict = {"okf_title": p.title}
    if p.links:
        meta["okf_links"] = list(p.links)
    return MemoryItem(item_id=p.item_id, content=p.content, metadata=meta)


def _round_trip(policy: str, *, k: int = K) -> dict:
    """Write every pair's memory via route_write(policy), then route each query; return per-pair hits."""
    with tempfile.TemporaryDirectory() as tmp:
        backends = {
            MARKDOWN: MarkdownStore(os.path.join(tmp, "md")),
            VECTORS: SqliteVectorStore(),
            GRAPH: GraphStore(),
        }
        router = Router.with_config(backends=backends, config=RouterConfig(write_policy=policy))
        writes = 0
        for p in PAIRS:
            item = _mk_item(p)
            for store in router.route_write(item):
                store.write(item)
                writes += 1
        hit_by_id: dict = {}
        for p in PAIRS:
            ids = {r.item_id for r in router.route(p.matching_query).search(p.matching_query, k=k)}
            hit_by_id[p.item_id] = p.item_id in ids
        backends[VECTORS].close()
    recall = sum(hit_by_id.values()) / len(hit_by_id) if hit_by_id else 0.0
    return {"policy": policy, "recall": recall, "writes": writes, "hit_by_id": hit_by_id}


class WriteRoutingContractTests(unittest.TestCase):
    def test_fixture_size_locked(self) -> None:
        self.assertEqual(len(PAIRS), _EXPECTED_PAIRS, "pair count changed — update _EXPECTED_PAIRS")
        ids = [p.item_id for p in PAIRS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate pair item_id")

    def test_default_write_policy_is_base_all(self) -> None:
        # D023: the recall-safe default writes every index.
        self.assertEqual(RouterConfig().write_policy, "base_all")

    def test_write_plan_per_policy(self) -> None:
        item = MemoryItem(item_id="x", content="the billing worker depends on the ledger database")
        base_all = Router.with_config(config=RouterConfig(write_policy="base_all"))
        base_sel = Router.with_config(config=RouterConfig(write_policy="base_selective"))
        single = Router.with_config(config=RouterConfig(write_policy="single"))
        self.assertEqual(set(base_all.write_plan(item)), {MARKDOWN, VECTORS, GRAPH})
        self.assertEqual(base_all.write_plan(item)[0], MARKDOWN, "markdown is the base, written first")
        self.assertIn(MARKDOWN, base_sel.write_plan(item), "base_selective always includes the base")
        self.assertLessEqual(len(base_sel.write_plan(item)), 2)
        self.assertEqual(len(single.write_plan(item)), 1, "single = only the classified backend")

    def test_unknown_policy_raises(self) -> None:
        r = Router.with_config(config=RouterConfig(write_policy="bogus"))
        with self.assertRaises(ValueError):
            r.write_plan(MemoryItem(item_id="x", content="hi"))

    def test_route_write_raises_with_no_backends(self) -> None:
        r = Router.with_config(config=RouterConfig(write_policy="base_all"))  # no backends
        with self.assertRaises(RuntimeError):
            r.route_write(MemoryItem(item_id="x", content="hi"))


class WriteRoutingRoundTripTests(unittest.TestCase):
    """The headline metric: write a routed memory, then route its query — does it come back?"""

    def test_base_all_round_trips_every_pair(self) -> None:
        # The accuracy guarantee: writing every index makes a memory retrievable whatever backend its
        # query routes to. base_all must round-trip ALL pairs.
        r = _round_trip("base_all")
        misses = [pid for pid, hit in r["hit_by_id"].items() if not hit]
        self.assertEqual(r["recall"], 1.0, f"base_all must round-trip every pair; missed: {misses}")

    def test_selective_loses_recall_vs_base_all_anti_theater(self) -> None:
        # Anti-theater: the policy choice is REAL. base_selective (place only where content classifies)
        # measurably loses round-trip recall vs base_all, because content- and query-classification
        # diverge — so a recall-safe default MUST write every index. If this ever stops being true,
        # the default could be revisited; until then it justifies base_all.
        base_all = _round_trip("base_all")
        selective = _round_trip("base_selective")
        self.assertEqual(base_all["recall"], 1.0)
        self.assertLess(selective["recall"], base_all["recall"],
                        "base_selective should lose recall vs base_all (write/query classify diverge)")
        recovered = [pid for pid in selective["hit_by_id"]
                     if not selective["hit_by_id"][pid] and base_all["hit_by_id"][pid]]
        self.assertGreaterEqual(len(recovered), 5,
                                "base_all must recover the pairs base_selective misses (currently 7; "
                                "floored at 5 to flag drift while tolerating minor classifier shifts)")
        self.assertLess(selective["writes"], base_all["writes"], "selective writes fewer indexes")


def _report() -> None:
    print(f"Write-routing round-trip eval (D023) — {len(PAIRS)} pairs (k={K}).\n")
    print(f"{'policy':<16} {'recall':>7} {'writes':>7} {'writes/mem':>11}")
    for pol in ("single", "base_selective", "base_all"):
        r = _round_trip(pol)
        print(f"{pol:<16} {r['recall']:>7.3f} {r['writes']:>7} {r['writes']/len(PAIRS):>11.2f}")
    print("\nbase_all is the recall-safe default: selective placement loses round-trip recall because "
          "content- and query-classification diverge under the rule classifier (D023). Offline + "
          "deterministic; markdown base uses a tempdir.")


if __name__ == "__main__":
    _report()
