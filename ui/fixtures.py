"""Synthetic demo-corpus writer for the inspector (``--seed``).

Writes ~10 memories through the REAL write path (``RouterStore(router).write`` →
dedup + write-routing → ``base_all`` fan-out to all three backends) plus four linked
target nodes (so graph edges resolve) and TWO deliberate anomalies:

1. **Fan-out asymmetry** — one item written DIRECTLY to a single backend
   (``backends["markdown"].write``), bypassing the router. Its on-disk landing
   (markdown only) ≠ its ``write_plan`` (base_all → all three) → the ⚠ asymmetry the
   routing view exists to surface.
2. **Intent mismatch** — one human-labelled *relational* item whose content carries no
   relational signal, so the rule classifier confidently routes it elsewhere
   (markdown). Surfaced as ``classify ≠ human_intent``.

Corpus content is generic (non-project) and uses STABLE ids and deterministic
timestamps so a seeded demo is reproducible. Writes ONLY into the seeded demo dir —
never a real substrate (the caller guards ``results/``).
"""

from __future__ import annotations

from pathlib import Path

from memeval.router import GRAPH, MARKDOWN, VECTORS, Router, RouterStore, fusion_profile
from memeval.schema import MemoryItem
from memeval.stores import GraphStore, MarkdownStore, SqliteVectorStore

# Deterministic timeline: 2024-01-01 00:00:00 UTC, one day apart per item.
_T0 = 1_704_067_200.0
_DAY = 86_400.0


def _t(day: int) -> float:
    return _T0 + day * _DAY


def _demo_items() -> list:
    """The ~10 routed memories + four link targets. Each item's content is crafted so the
    rule classifier routes it to the labelled backend (see the per-item comments)."""
    return [
        # -- literal / markdown (code tokens, filenames, exact values) -> markdown -----
        MemoryItem(item_id="retry-max-attempts", timestamp=_t(0), version=1, relevancy=0.95,
                   tags=["payments", "config"], source="note",
                   content="MAX_RETRIES = 5 in payment_config.py for the charge endpoint."),
        MemoryItem(item_id="staging-env-flags", timestamp=_t(1), version=2, relevancy=0.8,
                   tags=["staging", "config"], source="note",
                   content="Set DATABASE_URL and CACHE_TTL in staging_config.py before deploy."),
        MemoryItem(item_id="jwt-signature", timestamp=_t(2), version=1, relevancy=0.9,
                   tags=["auth"], source="note",
                   content="The validateJwt signature returns bool and takes a token string."),
        # -- conceptual / rationale -> vectors -----------------------------------------
        MemoryItem(item_id="why-rrf-fusion", timestamp=_t(3), version=1, relevancy=1.0,
                   tags=["retrieval", "decision"], source="decision",
                   content="Why we chose RRF fusion over score-normalization: better recall "
                           "robustness across backends with different score scales."),
        MemoryItem(item_id="cdn-cache-tradeoff", timestamp=_t(4), version=1, relevancy=0.85,
                   tags=["caching", "decision"], source="decision",
                   content="The tradeoffs of caching at the CDN edge versus the app layer, and "
                           "why we leaned on the edge for read-heavy paths."),
        MemoryItem(item_id="offline-writes-rationale", timestamp=_t(5), version=1, relevancy=0.7,
                   tags=["mobile", "decision"], source="decision",
                   content="The reasoning behind not supporting offline writes in the mobile app."),
        # -- relational -> graph (okf_links + the target nodes below) ------------------
        MemoryItem(item_id="payment-depends-retry", timestamp=_t(6), version=1, relevancy=0.95,
                   tags=["payments", "architecture"], source="note",
                   content="PaymentService depends on the RetryQueue for durable delivery.",
                   metadata={"okf_links": [("depends on", "retry-queue")]}),
        MemoryItem(item_id="auth-imports-crypto", timestamp=_t(7), version=1, relevancy=0.9,
                   tags=["auth", "architecture"], source="note",
                   content="AuthService imports the crypto helper module for hashing.",
                   metadata={"okf_links": [("imports", "crypto-helper")]}),
        # -- ambiguous (competing signals -> low margin, flagged) ----------------------
        MemoryItem(item_id="auth-why-depends", timestamp=_t(8), version=1, relevancy=0.6,
                   tags=["auth", "ambiguous"], source="note",
                   content="Why does the auth service depend on the session cache?",
                   metadata={"okf_links": [("depends on", "session-cache")]}),
        MemoryItem(item_id="ratelimiter-import-why", timestamp=_t(9), version=1, relevancy=0.6,
                   tags=["ratelimit", "ambiguous"], source="note",
                   content="What does the rate limiter import and why?",
                   metadata={"okf_links": [("imports", "token-bucket")]}),
        # -- link targets (so graph edges resolve to real nodes) -----------------------
        MemoryItem(item_id="retry-queue", timestamp=_t(10), version=1, relevancy=0.8,
                   tags=["payments"], source="note",
                   content="RetryQueue buffers failed payment jobs for redelivery."),
        MemoryItem(item_id="crypto-helper", timestamp=_t(11), version=1, relevancy=0.8,
                   tags=["auth"], source="note",
                   content="crypto_helper wraps the libsodium primitives we depend on."),
        MemoryItem(item_id="session-cache", timestamp=_t(12), version=1, relevancy=0.8,
                   tags=["auth"], source="note",
                   content="SessionCache stores ephemeral auth tokens in memory."),
        MemoryItem(item_id="token-bucket", timestamp=_t(13), version=1, relevancy=0.8,
                   tags=["ratelimit"], source="note",
                   content="token_bucket throttles request bursts per client."),
    ]


def _anomaly_intent_mismatch() -> MemoryItem:
    """A human-labelled *relational* memory whose content has no relational signal, so the
    rule classifier routes it confidently to markdown (code tokens) — ``classify ≠
    human_intent``. Written via the router (base_all), so its landing matches its plan; the
    anomaly is the classify/intent disagreement, not a fan-out asymmetry."""
    return MemoryItem(
        item_id="anomaly-intent-mismatch", timestamp=_t(14), version=1, relevancy=0.5,
        tags=["resilience", "anomaly"], source="note",
        content="The circuit_breaker module and retry_budget config both improve checkout resilience.",
        metadata={"human_intent": GRAPH,
                  "note": "Human labelled this relational; the rule classifier sends it to markdown."},
    )


def _anomaly_direct_write() -> MemoryItem:
    """A memory written DIRECTLY to a single backend (markdown), bypassing the router, so its
    on-disk landing is markdown-only while its write_plan is base_all → the ⚠ asymmetry."""
    return MemoryItem(
        item_id="anomaly-direct-markdown", timestamp=_t(15), version=1, relevancy=0.5,
        tags=["staging", "anomaly"], source="note",
        content="HOTFIX_FLAG=true disables the email_step in staging_config.py.",
        metadata={"note": "Written directly to markdown only, bypassing the router."},
    )


def seed(store_dir: str) -> dict:
    """Build the demo corpus under ``store_dir`` and return a small manifest.

    Uses the offline fusion profile (no embedder/key needed) and the default ``base_all``
    write policy, so the routed memories fan out to all three backends. Closes the
    durable stores so the files are flushed for the inspector to reopen. The
    refuse-to-seed-inside-``results/`` guard lives in ``__main__`` (the caller).
    """
    root = Path(store_dir)
    root.mkdir(parents=True, exist_ok=True)
    backends = {
        MARKDOWN: MarkdownStore(root / "markdown"),
        VECTORS: SqliteVectorStore(str(root / "memory.db")),
        GRAPH: GraphStore(path=str(root / "graph.db")),
    }
    router = Router.with_config(backends, fusion_profile())
    engine = RouterStore(router)

    items = _demo_items()
    for item in items:
        engine.write(item)                      # real write path → base_all fan-out

    intent = _anomaly_intent_mismatch()
    engine.write(intent)                        # routed (base_all), but classify ≠ human_intent

    direct = _anomaly_direct_write()
    backends[MARKDOWN].write(direct)            # anomaly: single-backend, bypasses the router

    # Flush durable mirrors so the inspector reopens a consistent on-disk substrate.
    for store in (backends[VECTORS], backends[GRAPH]):
        close = getattr(store, "close", None)
        if callable(close):
            close()

    return {
        "store_dir": str(root),
        "routed_items": len(items) + 1,        # demo items + the intent-mismatch anomaly
        "direct_writes": 1,
        "anomalies": ["anomaly-direct-markdown (fan-out asymmetry)",
                      "anomaly-intent-mismatch (classify ≠ human_intent)"],
        "total_written": len(items) + 2,
    }


__all__ = ["seed"]
