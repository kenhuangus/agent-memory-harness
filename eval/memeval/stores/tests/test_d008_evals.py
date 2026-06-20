"""Durable D008 retrieval/gate eval — the graph→vector cascade's baseline fixture. Owner: Brent.

This is **PR1 of D008 (eval-first)**: a durable case set + baseline reporter for the
graph→vector *cascade* the router will grow in PR2. It encodes the gate/projection
semantics decided in DECISION_LOG D008 (ruled 2026-06-20) and measures them against
the EXISTING ``GraphStore`` + ``SqliteVectorStore`` — there is **no cascade wrapper
yet**. The numbers printed here are the baseline PR2's real cascade must match
(graph-only / vector-only recall+MRR within ~2pts) and beat (the gate's job is to turn
graph false-positives into vector recoveries with *zero* silent-wrong-successes).

Decided design (implemented here as a *test-local reference*, NOT in router.py):

* **Engagement scope** — the cascade engages only when ``classify(query) == GRAPH`` and
  both graph+vector backends exist. Every case here is a GRAPH-classified query (the
  contract test guards this); markdown/vector queries keep single-route behavior and are
  out of scope for this fixture.
* **Gate = exact-anchor** — graph "wins" (``accept_graph``) only when the graph rank-0 hit
  is a **unique exact anchor**: an ``item_id`` / OKF title / OKF resource basename that a
  quoted-or-backticked span in the query names *exactly* (modulo case and separators),
  resolving to exactly ONE item, AND clearing a calibrated score+margin floor. Anything
  else **falls through to vector**. Zero false-accepts on the hard cases is the hard
  blocker — this is the silent-wrong-success defense, so a wrong/ambiguous/absent anchor
  must NEVER be accepted just because the graph returned a confident lexical hit.
* **Projection = item_id hydration** — accepted graph hits → ranked ``item_id``s → hydrate
  each via the vector store's ``get(item_id)`` (fall back to the graph item if absent) →
  ``RetrievedItem``s, exact seed first, linked neighbors after by graph score.

Reproduce the report:    cd eval && python3 -m memeval.stores.tests.test_d008_evals
Run the regression guard: cd eval && python3 -m unittest memeval.stores.tests.test_d008_evals

Eval-first caveat: single-route baselines are EXPECTED to underperform what the cascade
will do — that is the point of a baseline. Vector recovery numbers are reported, not
asserted (offline ``_HashingEmbedder`` path; the real-embedding ``embed=`` path should be
re-run later). The ONE hard assertion is false-accepts == 0 on the hard cases; if a case
ever trips the reference gate, the report and the test surface it — they do not hide it.
"""

from __future__ import annotations

import re
import unittest
from dataclasses import dataclass
from typing import Optional

from memeval.router import Router
from memeval.schema import MemoryItem, RetrievedItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.sqlite_store import SqliteVectorStore

ACCEPT = "accept_graph"
FALLTHROUGH = "fallthrough_vector"
GRAPH = "graph"
K = 5  # top-k for every stage (graph search, vector search, gate, projection)

# -- reference exact-anchor gate floors (calibrated on the accept cases below) --------
# The identity match + "rank-0 IS the anchor" check do the heavy lifting; these floors
# are a secondary guard that rejects a degenerate or tied rank-0. Set just under the
# weakest accept case (rank-0 score ~0.37, margin ~0.18) and comfortably above zero, so
# every accept case clears them and no fall-through case could sneak an accept on score.
SCORE_FLOOR = 0.10
MARGIN_FLOOR = 0.05


# --------------------------------------------------------------------------- #
# Fixture item + case builders
# --------------------------------------------------------------------------- #
def _item(item_id: str, content: str, *, title: Optional[str] = None,
          resource: Optional[str] = None, links=None, timestamp: float = 0.0,
          relevancy: float = 1.0) -> MemoryItem:
    """A MemoryItem carrying OKF metadata (title/resource/links) the way okf.py emits it."""
    meta: dict = {}
    if title is not None:
        meta["okf_title"] = title
    if resource is not None:
        meta["okf_resource"] = resource
    if links:
        meta["okf_links"] = list(links)
    return MemoryItem(item_id=item_id, content=content, timestamp=timestamp,
                      relevancy=relevancy, metadata=meta)


@dataclass(frozen=True)
class Case:
    """One durable D008 cascade case. ``hard`` cases count toward the zero-false-accept blocker."""

    name: str
    slice_id: str
    query: str
    items: tuple
    gold_item_ids: tuple
    expected_gate: str               # ACCEPT | FALLTHROUGH
    note: str
    hard: bool = False               # a fall-through that MUST NOT be accepted
    as_of: Optional[float] = None
    vector_omit: tuple = ()          # item_ids written to graph but NOT to the vector store


# The seven D008 slices (the contract test asserts each appears at least once).
SLICE_EXACT = "exact_anchor"                     # exact graph anchor succeeds + projects links
SLICE_FALSE_POS = "graph_false_positive"         # graph route was wrong → fall through, vector recovers
SLICE_AMBIGUOUS = "ambiguous_anchors"            # multiple graph anchors → fall through
SLICE_WRONG_SEED = "wrong_lexical_seed"          # decoy outranks anchor → fall through (not silent success)
SLICE_AS_OF = "as_of_future"                     # as_of hides the future hit from both stages
SLICE_HYDRATION = "missing_vector_hydration"     # accept, but vector lacks an id → graph fallback
SLICE_EMPTY = "empty_graph_stage"                # graph returns nothing → fall through

_EXPECTED_SLICES = {
    SLICE_EXACT, SLICE_FALSE_POS, SLICE_AMBIGUOUS, SLICE_WRONG_SEED,
    SLICE_AS_OF, SLICE_HYDRATION, SLICE_EMPTY,
}


D008_CASES: tuple = (
    # -- Slice 1: exact graph anchor succeeds + projects linked candidates (accept) -------
    Case(
        name="exact_anchor_title_backtick",
        slice_id=SLICE_EXACT,
        query="modules that depend on `PaymentService`",
        items=(
            _item("payment-service",
                  "PaymentService module; the modules that depend on PaymentService "
                  "route charges through the retry queue",
                  title="PaymentService", resource="memeval://memory/payment-service",
                  links=["retry-queue"]),
            _item("retry-queue",
                  "retry queue buffers failed charge attempts for a later retry",
                  title="RetryQueue", resource="memeval://memory/retry-queue"),
            _item("auth-guard", "AuthGuard validates the session token per request",
                  title="AuthGuard"),
        ),
        gold_item_ids=("payment-service", "retry-queue"),  # seed + linked neighbor (projection)
        expected_gate=ACCEPT,
        note="backtick span == okf_title 'PaymentService'; rank-0 seed projects its linked neighbor",
    ),
    # -- Slice 1 (again): item_id anchor via a QUOTED span (accept) -----------------------
    Case(
        name="exact_anchor_itemid_quoted",
        slice_id=SLICE_EXACT,
        query='what depends on "auth-guard"',
        items=(
            _item("auth-guard",
                  "AuthGuard; what depends on auth guard is the session checks and handlers",
                  title="AuthGuard", links=["session-store"]),
            _item("session-store", "SessionStore keeps issued tokens for sessions",
                  title="SessionStore"),
            _item("payment-service", "PaymentService captures charges", title="PaymentService"),
        ),
        gold_item_ids=("auth-guard", "session-store"),
        expected_gate=ACCEPT,
        note="quoted span == item_id 'auth-guard' (case/sep-insensitive); exercises quoted+item_id anchoring",
    ),
    # -- Slice 2: graph false-positive route falls through, vector recovers (HARD) ---------
    Case(
        name="false_positive_no_anchor",
        slice_id=SLICE_FALSE_POS,
        query="how does our caching layer relate to the rate limiter",
        items=(
            _item("caching-rationale",
                  "our caching layer caches the limiter's rate decisions so the rate limiter "
                  "need not recompute them",
                  title="CachingRationale"),
            # A lexical FALSE-POSITIVE: it shares only the query's *connective* words ("how
            # does our ... layer relate to the") but is about a different subsystem. The graph's
            # token-overlap seeding still ranks it rank-0 (Jaccard ~0.54 vs the gold's ~0.33),
            # so naive accept-any-graph would surface the WRONG broker doc. The gold caching
            # note has the lower token-overlap but the stronger char-n-gram match, so the vector
            # store recovers it at rank-0.
            _item("broker-decoy",
                  "how does our data layer relate to the message broker",
                  title="BrokerNotes"),
        ),
        gold_item_ids=("caching-rationale",),
        expected_gate=FALLTHROUGH,
        hard=True,
        note="GRAPH-classified ('relate'); graph rank-0 is 'broker-decoy' (a lexical false-positive "
             "that shares only the query's connective words, NOT the gold) → no explicit anchor AND "
             "rank-0 != gold → must fall through. The vector store recovers the real "
             "'caching-rationale' note at rank-0. A naive accept-any-graph cascade would silently "
             "return the wrong broker doc (verified: graph_top=['broker-decoy','caching-rationale']).",
    ),
    # -- Slice 3: ambiguous multiple graph anchors → fall through (HARD) -------------------
    Case(
        name="ambiguous_two_anchors",
        slice_id=SLICE_AMBIGUOUS,
        query="how do `TokenBucket` and `RetryQueue` relate",
        items=(
            _item("token-bucket", "TokenBucket throttles requests per second",
                  title="TokenBucket"),
            _item("retry-queue", "RetryQueue re-attempts failed work items",
                  title="RetryQueue"),
            # A lexical decoy that names BOTH anchors but is the WRONG (deprecated) source.
            # It shares the most query tokens, so the graph ranks it rank-0 — ahead of the two
            # gold component docs — even though it is not gold. This makes the ambiguity
            # observably wrong: naive accept-any-graph would project the deprecated bridge as
            # its top hit instead of the two components.
            _item("legacy-bridge",
                  "how do the old TokenBucket and RetryQueue stubs relate in the deprecated "
                  "bridge we no longer use",
                  title="LegacyBridge"),
        ),
        gold_item_ids=("token-bucket", "retry-queue"),
        expected_gate=FALLTHROUGH,
        hard=True,
        note="two backticked spans resolve to two distinct items → not a UNIQUE anchor → fall "
             "through. A lexical decoy 'legacy-bridge' (the deprecated source) is graph rank-0, "
             "ahead of BOTH gold items, so naive accept-any-graph would project "
             "['legacy-bridge','token-bucket'] — surfacing the wrong deprecated doc and missing "
             "a gold component (silent-wrong-success). Vector returns both gold in its top-k.",
    ),
    # -- Slice 4: wrong lexical seed → fall through, NOT silent success (HARD) -------------
    Case(
        name="wrong_lexical_seed_decoy",
        slice_id=SLICE_WRONG_SEED,
        query="what calls `findActive`",
        items=(
            # the anchored item exists (title 'findActive') ...
            _item("user-repo-findactive",
                  "findActive on UserRepository returns active rows",
                  title="findActive"),
            # ... but a lexical decoy outranks it on raw token overlap with the query.
            _item("build-active-scanner",
                  "what calls buildActive and findActive in the active scanner",
                  title="buildActive"),
            _item("webhook-handler",
                  "the webhook handler is what calls findActive now after the split",
                  title="WebhookHandler"),
        ),
        gold_item_ids=("user-repo-findactive",),
        expected_gate=FALLTHROUGH,
        hard=True,
        note="anchor 'findActive' resolves to a real item, but a decoy is graph rank-0 → "
             "rank-0 != anchor → fall through; a naive 'accept the graph top hit' cascade "
             "would silently return the decoy",
    ),
    # -- Slice 5: as_of prevents future graph/vector hits → fall through (HARD) ------------
    Case(
        name="as_of_hides_future",
        slice_id=SLICE_AS_OF,
        query="what does `FutureService` depend on",
        items=(
            _item("future-service",
                  "FutureService depends on the new billing module",
                  title="FutureService", links=["billing-module"], timestamp=5000.0),
            _item("billing-module", "billing module handles invoices", timestamp=5000.0,
                  title="BillingModule"),
            _item("settings-doc", "settings configuration values for the app",
                  title="Settings", timestamp=100.0),
        ),
        gold_item_ids=("future-service",),
        expected_gate=FALLTHROUGH,
        hard=True,
        as_of=1000.0,
        note="as_of=1000 hides the future (ts=5000) anchor from BOTH stages → graph empty → "
             "fall through; vector recovery is correctly 0 (the memory did not exist yet)",
    ),
    # -- Slice 6: accept, but vector hydration is missing → graph fallback (reported) ------
    Case(
        name="hydration_fallback",
        slice_id=SLICE_HYDRATION,
        query="what imports `TokenBucket`",
        items=(
            _item("token-bucket",
                  "TokenBucket; what imports TokenBucket is the gateway rate limiter",
                  title="TokenBucket", resource="memeval://memory/token-bucket",
                  links=["rate-limiter"]),
            _item("rate-limiter", "RateLimiter uses leaky bucket and token strategies",
                  title="RateLimiter"),
        ),
        gold_item_ids=("token-bucket", "rate-limiter"),
        expected_gate=ACCEPT,
        vector_omit=("rate-limiter",),  # linked neighbor absent from the vector store
        note="accepted anchor projects [token-bucket, rate-limiter]; rate-limiter is missing from "
             "the vector store → hydration falls back to the graph item (counted in the report)",
    ),
    # -- Slice 7: empty / no-signal graph stage → fall through ----------------------------
    Case(
        name="empty_graph_stage",
        slice_id=SLICE_EMPTY,
        query="what relates to `Quasar`",
        items=(
            _item("nebula-doc", "Nebula pipeline ingests astronomy frames nightly",
                  title="Nebula"),
            _item("pulsar-doc", "Pulsar timing array calibration notes",
                  title="Pulsar"),
        ),
        gold_item_ids=(),  # genuinely nothing relevant exists; correct behavior is empty
        expected_gate=FALLTHROUGH,
        hard=True,
        note="no item shares a token with the query → graph search is empty → fall through "
             "without erroring. The vector store still returns its nearest rows "
             "(['nebula-doc', 'pulsar-doc'], scores ~0) — it never returns [] for a non-empty "
             "embedding query — but NONE are gold (gold is empty), so the gate accepts/invents no "
             "memory, and recovery@k is correctly 0 (no false memory surfaced as relevant).",
    ),
)

_EXPECTED_CASES = len(D008_CASES)  # locks the denominator — adding/removing a case is deliberate
_VALID_GATES = {ACCEPT, FALLTHROUGH}


# --------------------------------------------------------------------------- #
# Reference exact-anchor gate (TEST-LOCAL — PR2 ports the real one into router.py)
# --------------------------------------------------------------------------- #
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")


def _norm_identity(text) -> str:
    """Collapse a string to its bare identifier (lowercase, no separators).

    So ``payment-service``, ``PaymentService``, ``payment_service`` and the basename of
    ``memeval://memory/payment-service`` all compare equal — matching a code identifier
    named in a query to the memory item that owns it, while still being an EXACT match
    (no fuzzy/substring matching).
    """
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _anchor_spans(query: str) -> list:
    """Explicit anchor spans the user named: quoted or backticked substrings."""
    spans = _BACKTICK_RE.findall(query or "") + _QUOTED_RE.findall(query or "")
    return [s.strip() for s in spans if s.strip()]


def _identity_index(items_by_id: dict) -> dict:
    """Map each item's identity strings (item_id / okf_title / resource basename) → {item_id}."""
    index: dict = {}
    for item_id, item in items_by_id.items():
        meta = item.metadata or {}
        keys = [item_id]
        if meta.get("okf_title"):
            keys.append(meta["okf_title"])
        if meta.get("okf_resource"):
            keys.append(str(meta["okf_resource"]).rstrip("/").rsplit("/", 1)[-1])
        for key in keys:
            norm = _norm_identity(key)
            if norm:
                index.setdefault(norm, set()).add(item_id)
    return index


@dataclass(frozen=True)
class GateResult:
    decision: str       # ACCEPT | FALLTHROUGH
    reason: str
    anchored_id: Optional[str]


def exact_anchor_gate(query: str, graph_hits: list, items_by_id: dict, *,
                      score_floor: float = SCORE_FLOOR,
                      margin_floor: float = MARGIN_FLOOR) -> GateResult:
    """Decide ``accept_graph`` vs ``fallthrough_vector`` for a GRAPH-routed query.

    Accept only when the graph rank-0 hit is a UNIQUE exact anchor that clears the floor.
    Every other path falls through — each ``reason`` maps to a D008 slice, so the report
    can show *why* a case fell through.
    """
    if not graph_hits:
        return GateResult(FALLTHROUGH, "empty_graph_stage", None)
    spans = _anchor_spans(query)
    if not spans:
        return GateResult(FALLTHROUGH, "no_explicit_anchor", None)

    index = _identity_index(items_by_id)
    resolved: set = set()
    for span in spans:
        resolved |= index.get(_norm_identity(span), set())
    if len(resolved) != 1:
        reason = "ambiguous_anchor" if len(resolved) > 1 else "no_unique_anchor"
        return GateResult(FALLTHROUGH, reason, None)

    anchored_id = next(iter(resolved))
    if graph_hits[0].item_id != anchored_id:
        # the explicitly-named anchor is NOT the graph's top hit — refuse (silent-wrong-success guard)
        return GateResult(FALLTHROUGH, "rank0_not_anchor", anchored_id)

    score0 = graph_hits[0].score
    margin = score0 - graph_hits[1].score if len(graph_hits) > 1 else score0
    if score0 < score_floor or margin < margin_floor:
        return GateResult(FALLTHROUGH, "below_floor", anchored_id)
    return GateResult(ACCEPT, "exact_anchor", anchored_id)


def project(accepted_hits: list, graph: GraphStore, vector: SqliteVectorStore):
    """Hydrate accepted graph hits via the vector store (graph fallback). Returns (items, n_fallback)."""
    projected: list = []
    fallbacks = 0
    for rank, hit in enumerate(accepted_hits):
        item = vector.get(hit.item_id)
        if item is None:
            item = graph.get(hit.item_id)
            fallbacks += 1
        if item is not None:
            projected.append(RetrievedItem(item=item, score=hit.score, rank=rank))
    return projected, fallbacks


# --------------------------------------------------------------------------- #
# Per-case evaluation (used by both the reporter and the tests)
# --------------------------------------------------------------------------- #
def _build_stores(case: Case):
    """Build a fresh GraphStore + SqliteVectorStore from a case's items (no cascade wrapper)."""
    graph = GraphStore()
    vector = SqliteVectorStore()  # :memory:, default stdlib _HashingEmbedder (offline)
    for item in case.items:
        graph.write(item)
        if item.item_id not in case.vector_omit:
            vector.write(item)
    return graph, vector


def _recall_at_k(ranked_ids: list, gold: tuple) -> float:
    if not gold:
        return 0.0
    found = sum(1 for g in gold if g in ranked_ids)
    return found / len(gold)


def _mrr(ranked_ids: list, gold: tuple) -> float:
    gold_set = set(gold)
    for idx, item_id in enumerate(ranked_ids):
        if item_id in gold_set:
            return 1.0 / (idx + 1)
    return 0.0


def _naive_accept_satisfies_gold(graph_ids: list, gold: tuple) -> bool:
    """Would a NAIVE 'accept any non-empty graph' cascade return the right answer?

    A naive cascade that accepts the graph whenever it is non-empty would surface the graph
    hits verbatim as its ranked answer. It *satisfies* the gold set only when the gold items
    occupy the leading ``len(gold)`` projected slots — i.e. the correct items are at the very
    top, with no decoy contaminating them and none of the gold missing. Anything else (a decoy
    at rank-0, or gold buried/absent) means naive-accept would return WRONG or INSUFFICIENT
    memory — exactly the silent-wrong-success the exact-anchor gate exists to prevent.

    This is the anti-theater predicate: a hard case is only *genuinely* hard if naive-accept
    fails it. (Returns False for empty gold — naive-accept can't "satisfy" an empty gold set,
    and such cases are excluded from the assertion below anyway since their graph is empty.)
    """
    if not gold:
        return False
    return set(graph_ids[: len(gold)]) == set(gold)


def evaluate(case: Case, *, k: int = K) -> dict:
    """Run the single-route stores + reference gate for one case; return a result dict."""
    graph, vector = _build_stores(case)
    items_by_id = {it.item_id: it for it in case.items}
    graph_hits = graph.search(case.query, k=k, as_of=case.as_of)
    vector_hits = vector.search(case.query, k=k, as_of=case.as_of)
    gate = exact_anchor_gate(case.query, graph_hits, items_by_id)

    projected: list = []
    fallbacks = 0
    if gate.decision == ACCEPT:
        projected, fallbacks = project(graph_hits, graph, vector)

    result = {
        "case": case,
        "classify": Router().classify(case.query),
        "graph_ids": [h.item_id for h in graph_hits],
        "vector_ids": [h.item_id for h in vector_hits],
        "gate": gate,
        "projected_ids": [h.item_id for h in projected],
        "fallbacks": fallbacks,
        "mem_tokens": sum(h.tokens for h in projected),
    }
    vector.close()
    return result


def score() -> dict:
    """Aggregate the D008 baseline metrics across all cases (mirrors test_routing_evals.score)."""
    results = [evaluate(c) for c in D008_CASES]

    accept_expected = [r for r in results if r["case"].expected_gate == ACCEPT]
    ft_expected = [r for r in results if r["case"].expected_gate == FALLTHROUGH]
    hard = [r for r in results if r["case"].hard]
    graded = [r for r in results if r["case"].gold_item_ids]  # cases with gold to measure
    ft_with_gold = [r for r in ft_expected if r["case"].gold_item_ids]

    # gate accept-recall@k: accept cases whose graph top-k contains ALL their gold ids.
    # (This is recall — "did the accepted route's graph top-k cover gold" — NOT precision;
    # it does not penalize extra non-gold hits in the top-k.)
    gate_accept_recall_hits = [r for r in accept_expected
                               if set(r["case"].gold_item_ids) <= set(r["graph_ids"])]
    # false accepts: a fall-through case the reference gate WRONGLY accepted
    false_accepts = [r for r in ft_expected if r["gate"].decision == ACCEPT]
    false_accepts_hard = [r for r in hard if r["gate"].decision == ACCEPT]
    # gate decision correctness overall (did we label accept/fall-through as designed?)
    gate_correct = [r for r in results if r["gate"].decision == r["case"].expected_gate]
    # fall-through recovery@k: vector returns >=1 gold for a fall-through case
    recovered = [r for r in ft_with_gold
                 if set(r["case"].gold_item_ids) & set(r["vector_ids"])]
    # anti-theater: hard cases that PRODUCE a graph result must be defeated by a naive
    # accept-any-graph cascade (graph rank-0 is a decoy, or gold is missing). If a hard
    # case's naive-accept already satisfies gold, the case is not actually hard.
    hard_nonempty_graph = [r for r in hard if r["graph_ids"]]
    naive_defeated = [r for r in hard_nonempty_graph
                      if not _naive_accept_satisfies_gold(r["graph_ids"], r["case"].gold_item_ids)]

    def _mean(vals):
        return sum(vals) / len(vals) if vals else 0.0

    graph_recall = _mean([_recall_at_k(r["graph_ids"], r["case"].gold_item_ids) for r in graded])
    vector_recall = _mean([_recall_at_k(r["vector_ids"], r["case"].gold_item_ids) for r in graded])
    graph_mrr = _mean([_mrr(r["graph_ids"], r["case"].gold_item_ids) for r in graded])
    vector_mrr = _mean([_mrr(r["vector_ids"], r["case"].gold_item_ids) for r in graded])

    return {
        "results": results,
        "n_cases": len(results),
        "n_accept": len(accept_expected),
        "n_fallthrough": len(ft_expected),
        "n_hard": len(hard),
        "gate_accept_recall_at_k": (len(gate_accept_recall_hits) / len(accept_expected))
        if accept_expected else 0.0,
        "false_accepts": false_accepts,
        "false_accepts_hard": false_accepts_hard,
        "gate_decision_accuracy": (len(gate_correct) / len(results)) if results else 0.0,
        "recovery_at_k": (len(recovered) / len(ft_with_gold)) if ft_with_gold else 0.0,
        "n_ft_with_gold": len(ft_with_gold),
        "n_hard_nonempty_graph": len(hard_nonempty_graph),
        "n_naive_defeated": len(naive_defeated),
        "graph_recall_at_k": graph_recall,
        "vector_recall_at_k": vector_recall,
        "graph_mrr": graph_mrr,
        "vector_mrr": vector_mrr,
        "graded": graded,
        "accept_expected": accept_expected,
    }


# --------------------------------------------------------------------------- #
# Tests (contract well-formedness + the false-accept blocker)
# --------------------------------------------------------------------------- #
class D008FixtureContractTests(unittest.TestCase):
    def test_fixture_contract_is_valid(self) -> None:
        seen_names: set = set()
        slices: set = set()
        for i, case in enumerate(D008_CASES):
            self.assertIsInstance(case, Case, f"case[{i}] must be a Case")
            self.assertTrue(case.name, f"case[{i}] missing name")
            self.assertNotIn(case.name, seen_names, f"case[{i}] duplicate name {case.name!r}")
            seen_names.add(case.name)
            self.assertIn(case.expected_gate, _VALID_GATES, f"{case.name}: bad expected_gate")
            self.assertIn(case.slice_id, _EXPECTED_SLICES, f"{case.name}: unknown slice {case.slice_id!r}")
            self.assertTrue(case.items, f"{case.name}: needs >=1 memory item")
            self.assertTrue(case.note.strip(), f"{case.name}: every case must carry a note")
            ids = {it.item_id for it in case.items}
            for gid in case.gold_item_ids:
                self.assertIn(gid, ids, f"{case.name}: gold id {gid!r} not among its items")
            for oid in case.vector_omit:
                self.assertIn(oid, ids, f"{case.name}: vector_omit id {oid!r} not among its items")
            # a fall-through case must not also be the accept-expected case (mutually exclusive)
            if case.expected_gate == ACCEPT:
                self.assertFalse(case.hard, f"{case.name}: accept cases are not 'hard' fall-throughs")
            slices.add(case.slice_id)
        self.assertEqual(len(D008_CASES), _EXPECTED_CASES,
                         "case count changed — update _EXPECTED_CASES deliberately")
        self.assertEqual(slices, _EXPECTED_SLICES,
                         f"all seven D008 slices must be covered; missing: {_EXPECTED_SLICES - slices}")

    def test_every_case_is_in_cascade_scope(self) -> None:
        # engagement scope: the cascade only engages on GRAPH-classified queries.
        router = Router()
        for case in D008_CASES:
            with self.subTest(case=case.name):
                self.assertEqual(router.classify(case.query), GRAPH,
                                 f"{case.name}: query is out of cascade scope (not GRAPH-routed)")

    def test_gate_decisions_match_expected(self) -> None:
        for r in score()["results"]:
            with self.subTest(case=r["case"].name):
                self.assertEqual(r["gate"].decision, r["case"].expected_gate,
                                 f"{r['case'].name}: gate={r['gate'].reason}")


class D008BlockerTests(unittest.TestCase):
    def test_zero_false_accepts_on_hard_cases(self) -> None:
        # THE hard blocker: the reference exact-anchor gate must never accept a hard
        # fall-through case (the silent-wrong-success defense).
        s = score()
        offenders = [(r["case"].name, r["gate"].reason) for r in s["false_accepts_hard"]]
        self.assertEqual(offenders, [], f"false-accepts on hard cases: {offenders}")

    def test_no_false_accepts_anywhere(self) -> None:
        s = score()
        offenders = [r["case"].name for r in s["false_accepts"]]
        self.assertEqual(offenders, [], f"unexpected accept on a fall-through case: {offenders}")

    def test_accept_cases_clear_the_floor(self) -> None:
        # the calibrated floor must not reject a legitimate accept case.
        for r in score()["accept_expected"]:
            with self.subTest(case=r["case"].name):
                self.assertEqual(r["gate"].decision, ACCEPT,
                                 f"{r['case'].name}: accept case fell through ({r['gate'].reason})")

    def test_hydration_fallback_is_reported(self) -> None:
        # the missing-vector-hydration slice must record a graph fallback, not crash.
        r = next(x for x in score()["results"] if x["case"].slice_id == SLICE_HYDRATION)
        self.assertEqual(r["gate"].decision, ACCEPT)
        self.assertGreaterEqual(r["fallbacks"], 1, "expected >=1 vector-hydration fallback")
        self.assertIn("rate-limiter", r["projected_ids"], "fallback item must still be projected")

    def test_as_of_hides_future_from_both_stages(self) -> None:
        r = next(x for x in score()["results"] if x["case"].slice_id == SLICE_AS_OF)
        self.assertNotIn("future-service", r["graph_ids"], "as_of must hide the future graph hit")
        self.assertNotIn("future-service", r["vector_ids"], "as_of must hide the future vector hit")
        self.assertEqual(r["gate"].decision, FALLTHROUGH)


class D008AntiTheaterTests(unittest.TestCase):
    """Machine-checked guard that the HARD cases are *actually* hard.

    The verifier caught a self-confirming version of this eval: two "hard" cases had the
    gold item as the graph's rank-0 hit, so a naive 'accept any non-empty graph' cascade
    would have returned the correct answer — the silent-wrong-success failure mode was never
    exercised. These tests turn "this case is hard" from a comment into a property CI enforces:
    for every hard fall-through case that produces a NON-EMPTY graph result, naive-accept must
    return WRONG or INSUFFICIENT gold. A future too-easy case fails here instead of passing
    silently and re-introducing the theater.
    """

    def test_hard_cases_defeat_naive_accept_any_graph(self) -> None:
        results = score()["results"]
        hard_nonempty = [r for r in results if r["case"].hard and r["graph_ids"]]
        # Guard against a vacuous pass: the fixture must actually exercise the
        # silent-wrong-success path on >=1 cases-with-a-graph-result per BLOCKING case
        # (false_positive, ambiguous, wrong_lexical_seed) plus any future additions.
        self.assertGreaterEqual(
            len(hard_nonempty), 3,
            "expected >=3 hard cases with a non-empty graph result (the silent-wrong-success "
            f"cases); got {[r['case'].name for r in hard_nonempty]}")
        for r in hard_nonempty:
            with self.subTest(case=r["case"].name):
                gold = r["case"].gold_item_ids
                self.assertTrue(
                    gold,
                    f"{r['case'].name}: a non-empty-graph hard case needs gold to test against")
                self.assertFalse(
                    _naive_accept_satisfies_gold(r["graph_ids"], gold),
                    f"{r['case'].name}: NOT actually hard — a naive accept-any-graph cascade would "
                    f"project {r['graph_ids'][:len(gold)]}, which already satisfies gold "
                    f"{list(gold)}. Graph rank-0 must be a decoy (or a gold item must be missing) "
                    "so the gate's fall-through is doing real work, not rubber-stamping a hit that "
                    "happens to be correct.")

    def test_empty_graph_hard_cases_are_genuinely_empty(self) -> None:
        # The two empty-graph hard cases (as_of, empty) carry no graph signal, so they are
        # *correctly* outside the naive-accept assertion above. Verify they are genuinely
        # empty — not silently dropped by a bug — and that they are the ONLY empty-graph
        # hard cases (a new empty-graph hard case must be added deliberately).
        results = score()["results"]
        empty_hard = [r for r in results if r["case"].hard and not r["graph_ids"]]
        self.assertEqual(
            {r["case"].slice_id for r in empty_hard}, {SLICE_AS_OF, SLICE_EMPTY},
            "the only hard cases with an empty graph must be the as_of + empty_graph slices; "
            f"got {sorted(r['case'].name for r in empty_hard)}")


# --------------------------------------------------------------------------- #
# Baseline report
# --------------------------------------------------------------------------- #
def _pct(x: float) -> str:
    return f"{round(100 * x)}%"


def _report() -> None:
    s = score()
    print(f"D008 cascade baseline — {s['n_cases']} cases "
          f"({s['n_accept']} accept, {s['n_fallthrough']} fall-through, {s['n_hard']} hard).")
    print("Stores: GraphStore + SqliteVectorStore (default _HashingEmbedder, offline). "
          "No cascade wrapper — single-route baselines + a TEST-LOCAL reference gate.\n")

    print("Per-case gate decisions (anchor → graph rank-0 → fall-through reason):")
    for r in s["results"]:
        c = r["case"]
        ok = "ok " if r["gate"].decision == c.expected_gate else "!! "
        print(f"  {ok}{c.slice_id:24} {c.name:28} -> {r['gate'].decision:18} "
              f"({r['gate'].reason}) graph_top={r['graph_ids'][:3]}")
    print()

    fa_hard = s["false_accepts_hard"]
    blocker = "PASS (0)" if not fa_hard else f"FAIL ({[r['case'].name for r in fa_hard]})"
    print("Gate metrics")
    print(f"  accept-recall@{K} (accept cases w/ ALL gold in graph top-k): "
          f"{_pct(s['gate_accept_recall_at_k'])} ({s['n_accept']} accept cases)")
    print(f"  *** false-accepts on HARD cases (MUST be 0): {blocker} ***")
    anti = ("PASS" if s["n_naive_defeated"] == s["n_hard_nonempty_graph"]
            else f"FAIL ({s['n_hard_nonempty_graph'] - s['n_naive_defeated']} not hard)")
    print(f"  *** anti-theater: naive accept-any-graph FAILS "
          f"{s['n_naive_defeated']}/{s['n_hard_nonempty_graph']} hard non-empty-graph cases "
          f"(MUST be all): {anti} ***")
    print(f"  false-accepts on any fall-through case:      {len(s['false_accepts'])}")
    print(f"  gate decision accuracy (vs expected_gate):   {_pct(s['gate_decision_accuracy'])}")
    print(f"  fall-through recovery@{K} (vector returns gold): "
          f"{_pct(s['recovery_at_k'])} ({s['n_ft_with_gold']} recoverable fall-through cases)\n")

    print("Single-route baselines (what PR2's cascade must match within ~2pts)")
    print(f"  graph-only  recall@{K} = {s['graph_recall_at_k']:.3f}   MRR = {s['graph_mrr']:.3f}")
    print(f"  vector-only recall@{K} = {s['vector_recall_at_k']:.3f}   MRR = {s['vector_mrr']:.3f}")
    print(f"  (over {len(s['graded'])} cases carrying gold ids)\n")

    print("Projection / memory-token overhead (accepted cases — RetrievedItem carries tokens)")
    for r in s["results"]:
        if r["gate"].decision == ACCEPT:
            print(f"  {r['case'].name:28} projected={r['projected_ids']} "
                  f"sum(tokens)={r['mem_tokens']} hydration_fallbacks={r['fallbacks']}")
    print()
    print("Notes: offline _HashingEmbedder path — re-run the real-embedding (embed=) path later. "
          "Vector recovery is reported, not asserted (eval-first baseline).")


if __name__ == "__main__":
    _report()
