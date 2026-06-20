"""Eval-first tests for the rule-based router (`memeval.router`). Owner: Brent.

Mirrors the seed routing eval set (query -> expected backend); the durable blind set is `test_routing_evals.py`
(we'll centralize to a JSONL fixture as it grows). Tiers:

* **golden** -> hard assertions: `classify()` MUST pick `expected`.
* **edge / adversarial** -> measured: we assert the v1 accuracy we actually
  designed for. NOTE (self-confirming caveat): passing the seed proves the rules
  are *consistent with our current hypotheses*, not that they generalize — the
  real signal is adversarial cases added LATER that we did not design around.
* **contested** (the ROUTING_EVALS ⚠ cases) -> NOT asserted for correctness; we
  only check `route()` returns a valid backend (their labels are provisional).

Lives under `stores/tests/` because `eval/tests/` is Ken's and there is not yet a
Brent-owned routing dir; relocate when we add `routing/` to CODEOWNERS.
Run from `eval/`:  `python3 -m unittest memeval.stores.tests.test_router`
"""

from __future__ import annotations

import unittest

from memeval.harness import InMemoryStore
from memeval.protocols import MemoryStore
from memeval.router import Router

MARKDOWN, GRAPH, VECTORS = "markdown", "graph", "vectors"

# (query, expected) — the seed routing eval set.
GOLDEN = [
    ("MemoryStore protocol", MARKDOWN),
    ("parse_frontmatter signature", MARKDOWN),
    ("TODO(brent) in stores", MARKDOWN),
    ("DEFAULT_BUDGET_USD", MARKDOWN),
    ("okf.py search method", MARKDOWN),
    ("what depends on schema.py", GRAPH),
    ("what calls Router.route", GRAPH),
    ("how is the dreaming worker connected to the stores", GRAPH),
    ("what conflicts with the offline-only guarantee", GRAPH),
    ("modules that use OKFStore", GRAPH),
    ("why did we choose keyword-only search", VECTORS),
    ("our reasoning on embedding model tradeoffs", VECTORS),
    ("notes about not peeking at future timestamps", VECTORS),
    ("summary of the OKF integration decision", VECTORS),
]

EDGE_ADVERSARIAL = [
    ("what should I work on next", VECTORS),
    ("Voyage embeddings", MARKDOWN),
    ("the schema freeze", MARKDOWN),
    ("what's related to the cost tracker", GRAPH),
    ("why is parse_frontmatter slow", VECTORS),
    ("what's the exact name of the frozen contract file", MARKDOWN),
    ("Router", MARKDOWN),
]

# ⚠ provisional labels — routing is reported, not asserted for correctness.
CONTESTED = [
    "everything that came up when we froze schema.py",
]

# Bucket B — contested cases adjudicated to vectors (synthesis/topical reads, not structural).
BUCKET_B = [
    ("everything we know about `AuthGuard` middleware", VECTORS),  # B1 (a fusion candidate)
    ("compare our chosen retry-backoff strategy to the exponential one we rejected", VECTORS),  # B2: compare = synthesis
    ("where did we note the tradeoff between `Postgres` and `DynamoDB`?", VECTORS),  # B3: drop between..and
    ("compare the markdown store to the sqlite store", VECTORS),  # was seed ⚠, resolved by B2
]

# Guard (independent-verifier request): a real graph signal must WIN over an incidental
# compare/between/about vector signal — the tie-break that keeps these from regressing graph routing.
COMPETING_SIGNAL_GUARD = [
    ("compare what depends on schema.py", GRAPH),
    ("everything we know about what imports the logger", GRAPH),
]

# Regressions from the blind adversarial round (2026-06-19): each FAILED on router
# v1; the rules were fixed to pass them. See test_routing_evals.py for the full blind set.
ADVERSARIAL_FIXED = [
    ("which modules import `TokenBucket`", GRAPH),                       # missing 'import' signal
    ("does anything still import the old auth helper or did the v2 thing replace all of them everywhere", GRAPH),
    ("what breaks if I rename `UserRepository.findActive`?", GRAPH),     # impact analysis
    ("the reasoning behind using `WAL_MODE=true` in the SQLite config", VECTORS),  # 'using' false-positive
    ("what was that flag called the one we set to true to skip the email step in staging", MARKDOWN),  # 'called' = naming
    ("env var name for the S3 bucket override", MARKDOWN),              # 'name for', not only 'name of'
    ("remind me how the queue consumer and the dead letter thing relate, does one feed the other or", GRAPH),  # 'relate' w/o 'to'
]

# Degenerate inputs must NOT route to markdown via the short-query rule firing on
# zero real tokens — they degrade to the semantic default.
DEGENERATE = ["", "   ", "???!!! \U0001f525\U0001f4a5\U0001f916"]

# Known limitation (NOT asserted): "connect to" is syntactically a graph signal, so
# topical use ("how the notes connect to the idea") routes graph; distinguishing it
# from a real structural link needs semantics, not rules. Tracked, not fixed in v1.


class RouterClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = Router()  # classify() needs no registered backends

    def test_golden_cases_all_route_correctly(self) -> None:
        for query, expected in GOLDEN:
            with self.subTest(query=query):
                self.assertEqual(self.router.classify(query), expected)

    def test_edge_and_adversarial_meet_v1_target(self) -> None:
        results = [(q, self.router.classify(q), exp) for q, exp in EDGE_ADVERSARIAL]
        acc = sum(got == exp for _, got, exp in results) / len(results)
        # v1 is designed to pass the full non-contested seed; the *real* test is
        # adversarial cases added later that we did NOT design around.
        self.assertEqual(acc, 1.0, msg=f"edge/adversarial routing: {results}")

    def test_contested_cases_return_a_valid_backend(self) -> None:
        for query in CONTESTED:
            with self.subTest(query=query):
                self.assertIn(self.router.classify(query), {MARKDOWN, GRAPH, VECTORS})

    def test_adversarial_fixed_cases(self) -> None:
        for query, expected in ADVERSARIAL_FIXED:
            with self.subTest(query=query):
                self.assertEqual(self.router.classify(query), expected)

    def test_degenerate_inputs_default_to_semantic_not_markdown(self) -> None:
        for query in DEGENERATE:
            with self.subTest(query=repr(query)):
                self.assertEqual(self.router.classify(query), VECTORS)

    def test_bucket_b_adjudicated_cases(self) -> None:
        for query, expected in BUCKET_B:
            with self.subTest(query=query):
                self.assertEqual(self.router.classify(query), expected)

    def test_real_graph_signal_wins_over_incidental_vector_signal(self) -> None:
        for query, expected in COMPETING_SIGNAL_GUARD:
            with self.subTest(query=query):
                self.assertEqual(self.router.classify(query), expected)


class RouterRouteTests(unittest.TestCase):
    def test_route_returns_the_chosen_backend_when_registered(self) -> None:
        md, vec = InMemoryStore(), InMemoryStore()
        r = Router({MARKDOWN: md, VECTORS: vec})
        self.assertIs(r.route("MemoryStore protocol"), md)            # -> markdown
        self.assertIs(r.route("why did we choose keyword-only search"), vec)  # -> vectors

    def test_route_falls_back_when_target_unavailable(self) -> None:
        md = InMemoryStore()  # only markdown registered
        r = Router({MARKDOWN: md})
        self.assertIs(r.route("why did we choose keyword-only search"), md)  # graceful

    def test_route_result_satisfies_memorystore(self) -> None:
        r = Router({MARKDOWN: InMemoryStore()})
        self.assertIsInstance(r.route("anything"), MemoryStore)

    def test_route_with_no_backends_raises(self) -> None:
        with self.assertRaises(Exception):
            Router({}).route("anything")


if __name__ == "__main__":
    unittest.main()
