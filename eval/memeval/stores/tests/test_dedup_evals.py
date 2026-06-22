"""Dedup-on-write eval (D024) — and why offline lexical dedup is UNSAFE. Owner: Brent.

Dedup-on-write (ADR-P2/P4, the "[storage/Brent]" open question): on write, a near-duplicate should
MERGE into the existing memory (reuse its id, newer content wins, version+1) instead of creating a
duplicate. `Router.write` implements the mechanism; this fixture measures whether it can be trusted on
the OFFLINE path.

**The finding (D024): it cannot.** With the stdlib char-n-gram embedder, the similarity of a reworded
true duplicate OVERLAPS the similarity of a *distinct-but-similar* memory — a distinct "read timeout 5s"
vs "write timeout 30s" (same sentence shape, one value changed) scores HIGHER than a genuinely reworded
duplicate. There is no threshold that catches real dups without also FALSE-MERGING distinct memories
(silent data loss). Char-trigram similarity ≠ same-fact — the same lesson as D019/D020/D023.

So **dedup defaults OFF** and is gated to a real semantic embedder (paid path), where same-fact vs
different-fact actually separate. The mechanism is built + correct (a verbatim duplicate merges; the
default-off config never merges), but auto-merging offline is a data-loss risk we refuse to ship on.

These tests (a) machine-check the overlap finding, (b) assert dedup defaults off, (c) prove the
mechanism merges a genuine (identical) duplicate when enabled, and (d) demonstrate the DANGER — a
permissive threshold false-merges a distinct pair — which is exactly why the default is off.

Provenance: 17 blind-generated cases (firewalled — near-dup "merge" pairs + distinct-but-similar
"no_merge" traps), threshold calibrated by a deterministic sweep. See DECISION_LOG D024.

Reproduce:    cd eval && python3 -m memeval.stores.tests.test_dedup_evals
Run the guard: cd eval && python3 -m unittest memeval.stores.tests.test_dedup_evals
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass

from memeval.router import GRAPH, MARKDOWN, VECTORS, Router, RouterConfig
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore


@dataclass(frozen=True)
class Case:
    """One dedup case: a stored BASE and a CANDIDATE write, with the expected verdict
    (``merge`` = candidate is a near-duplicate of base; ``no_merge`` = distinct, must not merge)."""

    name: str
    expected: str
    base_id: str
    base_content: str
    candidate_content: str


DEDUP_CASES = (
    Case(
        name='auth_session_token_ttl_reworded_merge', expected='merge',
        base_id='auth__session-token-ttl',
        base_content='The session token TTL is 1800 seconds.',
        candidate_content='Session tokens expire after 1800s (30 minutes).',
    ),
    Case(
        name='auth_password_bcrypt_cost_merge', expected='merge',
        base_id='auth__password-hash-bcrypt-cost',
        base_content='Passwords are hashed with bcrypt using a cost factor of 12.',
        candidate_content='We hash passwords using bcrypt with cost factor 12.',
    ),
    Case(
        name='pay_retry_backoff_merge', expected='merge',
        base_id='pay__payment-retry-backoff',
        base_content='Failed payment charges are retried up to 3 times with exponential backoff.',
        candidate_content='Failed payments retry a maximum of 3 times using exponential backoff.',
    ),
    Case(
        name='cache_redis_product_ttl_merge', expected='merge',
        base_id='cache__product-cache-ttl',
        base_content='The product catalog cache in Redis has a TTL of 5 minutes.',
        candidate_content='Product catalog entries are cached in Redis for 5 minutes (TTL 300s).',
    ),
    Case(
        name='db_pool_max_connections_merge', expected='merge',
        base_id='db__db-pool-max-connections',
        base_content='The database connection pool is configured with a maximum of 20 connections.',
        candidate_content='Max database connection pool size is 20 connections.',
    ),
    Case(
        name='deploy_main_branch_trigger_merge', expected='merge',
        base_id='deploy__deploy-trigger-branch',
        base_content='Merging to the main branch triggers an automatic production deployment.',
        candidate_content='A merge into main automatically deploys to production.',
    ),
    Case(
        name='search_index_refresh_interval_merge', expected='merge',
        base_id='search__search-index-refresh-interval',
        base_content='The Elasticsearch search index is refreshed every 30 seconds.',
        candidate_content='Search index in Elasticsearch refreshes on a 30 second interval.',
    ),
    Case(
        name='logs_app_retention_90d_merge', expected='merge',
        base_id='logs__app-log-retention',
        base_content='Application logs are retained for 90 days before deletion.',
        candidate_content='We keep application logs for 90 days, then delete them.',
    ),
    Case(
        name='ratelimit_api_per_key_merge', expected='merge',
        base_id='ratelimit__api-rate-limit',
        base_content='The public API enforces a rate limit of 100 requests per minute per API key.',
        candidate_content='Public API rate limit is 100 req/min for each API key.',
    ),
    Case(
        name='auth_session_vs_refresh_token_ttl_no_merge', expected='no_merge',
        base_id='auth__session-vs-refresh-token-ttl',
        base_content='Access tokens issued by the auth service expire after 1800 seconds and clients must obtain a new one after that.',
        candidate_content='Refresh tokens issued by the auth service are valid for 30 days before the user has to log in again.',
    ),
    Case(
        name='db_read_vs_write_timeout_no_merge', expected='no_merge',
        base_id='db__read-vs-write-timeout',
        base_content='Read queries against the primary Postgres database have a statement timeout of 5 seconds.',
        candidate_content='Write transactions against the primary Postgres database have a statement timeout of 30 seconds.',
    ),
    Case(
        name='pay_checkout_vs_refund_provider_no_merge', expected='no_merge',
        base_id='pay__checkout-vs-refund-provider',
        base_content='The checkout flow processes new card charges through Stripe.',
        candidate_content='The refund flow processes returns through Adyen rather than the original charge provider.',
    ),
    Case(
        name='ratelimit_gateway_vs_worker_no_merge', expected='no_merge',
        base_id='ratelimit__gateway-vs-worker',
        base_content='The public API gateway rate-limits each client to 100 requests per minute.',
        candidate_content='The internal background worker queue is throttled to 100 jobs per second by the scheduler.',
    ),
    Case(
        name='deploy_staging_vs_prod_strategy_no_merge', expected='no_merge',
        base_id='deploy__staging-vs-prod-strategy',
        base_content='Deploys to the staging environment use a rolling update with no manual approval.',
        candidate_content='Deploys to the production environment use blue-green with a required manual approval gate.',
    ),
    Case(
        name='cache_product_vs_order_ttl_no_merge', expected='no_merge',
        base_id='cache__product-vs-order-ttl',
        base_content='Product catalog entries are cached in Redis with a TTL of 1 hour.',
        candidate_content='Order status records are cached in Redis with a TTL of 60 seconds because they change frequently.',
    ),
    Case(
        name='logs_app_vs_audit_retention_no_merge', expected='no_merge',
        base_id='logs__app-vs-audit-retention',
        base_content='Application logs are retained in the logging pipeline for 14 days and then deleted.',
        candidate_content='Audit logs are retained for 7 years to satisfy compliance requirements.',
    ),
    Case(
        name='search_autocomplete_vs_full_engine_no_merge', expected='no_merge',
        base_id='search__autocomplete-vs-full-engine',
        base_content='The search box autocomplete suggestions are served from an in-memory trie for low latency.',
        candidate_content='Full search result pages are served from an Elasticsearch cluster that supports faceting and ranking.',
    ),
)


_EXPECTED_CASES = 17  # hardcoded size lock — changing the fixture is deliberate


def _candidate_to_base_similarity(case: "Case") -> float:
    """The cosine the dedup check keys on: write BASE into a fresh vector store, search CANDIDATE,
    take the top hit's score (the offline char-n-gram similarity)."""
    store = SqliteVectorStore()
    try:
        store.write(MemoryItem(item_id=case.base_id, content=case.base_content))
        hits = store.search(case.candidate_content, k=1)
        return hits[0].score if hits else 0.0
    finally:
        store.close()


def _scores() -> dict:
    merge = [_candidate_to_base_similarity(c) for c in DEDUP_CASES if c.expected == "merge"]
    no_merge = [_candidate_to_base_similarity(c) for c in DEDUP_CASES if c.expected == "no_merge"]
    return {"merge": merge, "no_merge": no_merge}


def _build_backends(tmp: str) -> dict:
    return {MARKDOWN: MarkdownStore(os.path.join(tmp, "md")),
            VECTORS: SqliteVectorStore(), GRAPH: GraphStore()}


class DedupContractTests(unittest.TestCase):
    def test_fixture_size_locked(self) -> None:
        self.assertEqual(len(DEDUP_CASES), _EXPECTED_CASES, "case count changed — update _EXPECTED_CASES")
        for c in DEDUP_CASES:
            self.assertIn(c.expected, ("merge", "no_merge"), f"{c.name}: bad expected")

    def test_dedup_defaults_off(self) -> None:
        # D024: offline lexical dedup is unsafe, so auto-merge is OFF by default (real-embedder-gated).
        self.assertFalse(RouterConfig().dedup, "dedup must default OFF (offline lexical dedup is unsafe)")


class DedupOfflineUnsafeTests(unittest.TestCase):
    """Machine-check the D024 finding: offline similarity cannot separate near-dups from distinct."""

    def test_no_threshold_separates_dups_from_distinct(self) -> None:
        s = _scores()
        best_dup, worst_distinct = max(s["merge"]), max(s["no_merge"])
        # The proof there is NO safe offline threshold: a threshold low enough to catch even the BEST
        # reworded duplicate (T <= best_dup) is necessarily <= the worst distinct pair's score, so it
        # FALSE-MERGES that distinct pair (silent data loss). Hence dedup is off by default offline.
        self.assertGreaterEqual(worst_distinct, best_dup,
                                f"catching the best reworded dup (score {best_dup:.3f}) needs a "
                                f"threshold that also false-merges the worst distinct pair "
                                f"(score {worst_distinct:.3f}) — no safe offline threshold exists")

    def test_zero_false_merge_threshold_catches_no_real_dups(self) -> None:
        # At the only threshold that guarantees zero false-merges (just above the worst distinct),
        # offline dedup recovers ~no reworded duplicates — so it cannot be safely turned on offline.
        s = _scores()
        safe_t = max(s["no_merge"]) + 1e-9
        merge_recall = sum(1 for x in s["merge"] if x >= safe_t) / len(s["merge"])
        self.assertLessEqual(merge_recall, 0.2,
                             "a zero-false-merge offline threshold should recover almost no real dups "
                             f"(got recall {merge_recall:.2f}) — proving offline dedup is unsafe")


class DedupMechanismTests(unittest.TestCase):
    """The mechanism is correct when similarity is genuine — and the danger is real at a low threshold."""

    def test_identical_content_merges_when_enabled(self) -> None:
        # A verbatim duplicate (cosine ~1.0) merges into the existing id, version bumped — the
        # mechanism works when the similarity is genuine.
        with tempfile.TemporaryDirectory() as tmp:
            be = _build_backends(tmp)
            r = Router.with_config(backends=be, config=RouterConfig(dedup=True))
            r1 = r.write(MemoryItem(item_id="m1", content="The session token TTL is 1800 seconds."))
            r2 = r.write(MemoryItem(item_id="m2", content="The session token TTL is 1800 seconds."))
            be[VECTORS].close()
            self.assertFalse(r1.merged)
            self.assertTrue(r2.merged, "a verbatim duplicate must merge when dedup is enabled")
            self.assertEqual(r2.item_id, "m1")
            self.assertEqual(r2.version, 2)

    def test_default_off_never_merges_distinct(self) -> None:
        # With the default config (dedup off), a distinct-but-similar candidate is stored separately —
        # no silent data loss on the offline path.
        case = next(c for c in DEDUP_CASES if c.expected == "no_merge")
        with tempfile.TemporaryDirectory() as tmp:
            be = _build_backends(tmp)
            r = Router.with_config(backends=be, config=RouterConfig())  # default: dedup off
            r.write(MemoryItem(item_id=case.base_id, content=case.base_content))
            rc = r.write(MemoryItem(item_id="cand", content=case.candidate_content))
            be[VECTORS].close()
            self.assertFalse(rc.merged, "default (dedup off) must never merge")
            self.assertEqual(rc.item_id, "cand", "distinct memory preserved under its own id")

    def test_permissive_threshold_false_merges_a_distinct_pair(self) -> None:
        # THE danger, demonstrated: at a permissive threshold, the highest-scoring distinct pair
        # false-merges (silent data loss) — which is precisely why dedup is off by default offline.
        scored = sorted(((_candidate_to_base_similarity(c), c)
                         for c in DEDUP_CASES if c.expected == "no_merge"), key=lambda t: t[0])
        worst_score, worst = scored[-1]
        t = worst_score - 0.05  # a threshold a naive tuner might pick to catch "near" dups
        with tempfile.TemporaryDirectory() as tmp:
            be = _build_backends(tmp)
            r = Router.with_config(backends=be,
                                   config=RouterConfig(dedup=True, dedup_threshold=t))
            r.write(MemoryItem(item_id=worst.base_id, content=worst.base_content))
            rc = r.write(MemoryItem(item_id="cand", content=worst.candidate_content))
            be[VECTORS].close()
            self.assertTrue(rc.merged,
                            f"{worst.name}: distinct pair false-merges at threshold {t:.3f} — the "
                            "data-loss risk that keeps dedup off by default offline")


def _report() -> None:
    s = _scores()
    print(f"Dedup-on-write eval (D024) — {len(DEDUP_CASES)} cases "
          f"({len(s['merge'])} merge, {len(s['no_merge'])} no_merge).\n")
    print(f"  merge (near-dup) cosine:    min {min(s['merge']):.3f}  max {max(s['merge']):.3f}")
    print(f"  no_merge (distinct) cosine: min {min(s['no_merge']):.3f}  max {max(s['no_merge']):.3f}")
    safe_t = max(s["no_merge"]) + 1e-9
    recall = sum(1 for x in s["merge"] if x >= safe_t) / len(s["merge"])
    print(f"  zero-false-merge threshold > {max(s['no_merge']):.3f} → merge-recall {recall:.2f}")
    print("\nOffline lexical similarity cannot separate near-dups from distinct-but-similar memories, "
          "so dedup defaults OFF (real-embedder-gated). The mechanism is correct; auto-merging offline "
          "would risk false merges = silent data loss (D024).")


if __name__ == "__main__":
    _report()
