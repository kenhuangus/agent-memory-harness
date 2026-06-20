"""Eval-first tests for the rule-based router (`memeval.router`). Owner: Brent.

Mirrors the seed routing eval set in `capstone-workspace/ROUTING_EVALS.md`
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

# (query, expected) — mirror of ROUTING_EVALS.md.
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
    "compare the markdown store to the sqlite store",
]


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
