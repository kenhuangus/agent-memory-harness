"""Mechanism test for ``SemanticRouterClassifier`` (PR3b-2) — owner: Brent.

CI-safe + stdlib-only. Proves the classifier's ROUTING LOGIC deterministically with a controllable
fake encoder (exemplars + queries placed at known points → predictable nearest-exemplar routing),
the document/query ``input_type`` asymmetry, the empty-query default, the ``RouterClassifier``
protocol fit, and the ``Router.with_config`` seam wiring. Plus a seam smoke: the real DEFAULT
exemplars run through the OFFLINE ``MockEmbedder`` end-to-end without error.

It deliberately does NOT assert semantic routing ACCURACY (multilingual GAP recovery etc.): offline
encoders are char-n-gram LEXICAL and cannot demonstrate the semantic win. That measurement is the
captained **D021** live bake-off (real Voyage), run out-of-band — never in CI (offline guarantee).

Run: cd eval && python3 -m unittest memeval.stores.tests.test_semantic_classifier
"""

from __future__ import annotations

import unittest

from memeval.router import (
    DEFAULT_ROUTING_EXEMPLARS,
    GRAPH,
    MARKDOWN,
    VECTORS,
    ClassificationResult,
    Router,
    RouterClassifier,
    RouterConfig,
    SemanticRouterClassifier,
)
from memeval.stores.embedders import MockEmbedder


class _FakeEncoder:
    """Deterministic encoder for testing the MECHANISM (not semantics).

    Maps a text to a fixed 3-d basis vector by a leading tag (``M:`` / ``G:`` / ``V:``), so exemplars
    and queries sit at known points and nearest-exemplar routing is fully predictable. Records every
    ``(text, input_type)`` call so the document/query asymmetry can be asserted.
    """

    # "GV" sits exactly between the graph and vectors basis vectors -> a genuine score tie.
    _BASIS = {"M": [1.0, 0.0, 0.0], "G": [0.0, 1.0, 0.0], "V": [0.0, 0.0, 1.0],
              "GV": [0.0, 1.0, 1.0]}

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, text, *, input_type=None):
        self.calls.append((text, input_type))
        tag = text.split(":", 1)[0] if ":" in text else "?"
        return list(self._BASIS.get(tag, [0.0, 0.0, 0.0]))


class _OneArgEncoder:
    """Legacy ``text -> vector`` encoder (no ``input_type`` kwarg) — the backward-compat seam."""

    def __call__(self, text):
        tag = text.split(":", 1)[0] if ":" in text else "?"
        return list(_FakeEncoder._BASIS.get(tag, [0.0, 0.0, 0.0]))


_FAKE_EXEMPLARS = {
    MARKDOWN: ("M:exact value of the timeout", "M:signature of foo()"),
    GRAPH: ("G:what depends on auth", "G:what calls billing"),
    VECTORS: ("V:why did we choose X", "V:summary of the approach"),
}


def _fake_clf(agg: str = "max") -> SemanticRouterClassifier:
    return SemanticRouterClassifier(_FakeEncoder(), exemplars=_FAKE_EXEMPLARS, agg=agg)


class SemanticClassifierMechanismTests(unittest.TestCase):
    def test_routes_each_backend_to_its_region(self) -> None:
        clf = _fake_clf()
        self.assertEqual(clf.classify("M:what's the exact port").choice, MARKDOWN)
        self.assertEqual(clf.classify("G:which jobs call the worker").choice, GRAPH)
        self.assertEqual(clf.classify("V:what's the rationale").choice, VECTORS)

    def test_result_has_scores_margin_and_nearest(self) -> None:
        r = _fake_clf().classify("G:depends on the cache")
        self.assertIsInstance(r, ClassificationResult)
        self.assertEqual(r.choice, GRAPH)
        self.assertAlmostEqual(r.scores[GRAPH], 1.0)   # query == graph basis vector
        self.assertAlmostEqual(r.scores[MARKDOWN], 0.0)
        self.assertAlmostEqual(r.margin, 1.0)
        self.assertIn(r.details["nearest_exemplar"][GRAPH], _FAKE_EXEMPLARS[GRAPH])

    def test_empty_query_routes_to_default(self) -> None:
        clf = SemanticRouterClassifier(_FakeEncoder(), exemplars=_FAKE_EXEMPLARS,
                                       default_backend=VECTORS)
        r = clf.classify("   ")
        self.assertEqual(r.choice, VECTORS)
        self.assertEqual(r.details.get("reason"), "empty_query")

    def test_document_query_input_type_asymmetry(self) -> None:
        enc = _FakeEncoder()
        clf = SemanticRouterClassifier(enc, exemplars=_FAKE_EXEMPLARS)
        # every exemplar was embedded as a document at construction
        self.assertTrue(enc.calls and all(it == "document" for _, it in enc.calls))
        enc.calls.clear()
        clf.classify("G:x")
        self.assertEqual(enc.calls, [("G:x", "query")])  # the query is embedded as a query

    def test_satisfies_router_classifier_protocol(self) -> None:
        self.assertIsInstance(_fake_clf(), RouterClassifier)

    def test_with_config_seam_wiring(self) -> None:
        router = Router.with_config(config=RouterConfig(classifier=_fake_clf()))
        self.assertEqual(router.classify("G:what depends on the queue"), GRAPH)
        self.assertEqual(router.classify("M:exact value of retries"), MARKDOWN)
        self.assertEqual(router.classify("V:why we picked this"), VECTORS)

    def test_mean_aggregation_runs(self) -> None:
        self.assertEqual(_fake_clf(agg="mean").classify("G:x").choice, GRAPH)

    def test_priority_breaks_a_genuine_tie(self) -> None:
        # "GV" embeds equidistant from the graph and vectors regions -> exact score tie; the
        # priority order (GRAPH before VECTORS) must decide deterministically.
        r = _fake_clf().classify("GV:ambiguous between relation and rationale")
        self.assertAlmostEqual(r.scores[GRAPH], r.scores[VECTORS])
        self.assertGreater(r.scores[GRAPH], r.scores[MARKDOWN])
        self.assertEqual(r.choice, GRAPH)

    def test_legacy_one_arg_encoder_is_called_positionally(self) -> None:
        # An encoder without an input_type kwarg must be detected as legacy and called text-only,
        # never crashing on a passed input_type.
        clf = SemanticRouterClassifier(_OneArgEncoder(), exemplars=_FAKE_EXEMPLARS)
        self.assertFalse(clf._accepts_input_type)
        self.assertEqual(clf.classify("G:what depends on the worker").choice, GRAPH)
        self.assertEqual(clf.classify("M:exact retry value").choice, MARKDOWN)

    def test_rejects_bad_agg_and_empty_exemplars(self) -> None:
        with self.assertRaises(ValueError):
            SemanticRouterClassifier(_FakeEncoder(), exemplars=_FAKE_EXEMPLARS, agg="nope")
        with self.assertRaises(ValueError):
            SemanticRouterClassifier(_FakeEncoder(),
                                     exemplars={GRAPH: (), MARKDOWN: ("M:x",), VECTORS: ("V:y",)})


class SemanticClassifierOfflineSeamSmokeTests(unittest.TestCase):
    """The real DEFAULT exemplars run through the OFFLINE ``MockEmbedder`` end-to-end without error.

    A SEAM smoke (plumbing), NOT an accuracy claim: ``MockEmbedder`` is char-n-gram lexical, so its
    routing is not the semantic routing the captained D021 run measures. We assert only that the
    classifier builds, classifies, and returns a valid backend for every default exemplar.
    """

    def test_default_exemplars_classify_offline_without_error(self) -> None:
        clf = SemanticRouterClassifier(MockEmbedder(dim=256), exemplars=DEFAULT_ROUTING_EXEMPLARS)
        valid = {GRAPH, MARKDOWN, VECTORS}
        for queries in DEFAULT_ROUTING_EXEMPLARS.values():
            for q in queries:
                r = clf.classify(q)
                self.assertIsInstance(r, ClassificationResult)
                self.assertIn(r.choice, valid)

    def test_default_exemplars_are_distinct_across_backends(self) -> None:
        seen: dict = {}
        for backend, queries in DEFAULT_ROUTING_EXEMPLARS.items():
            for q in queries:
                self.assertNotIn(q, seen, f"exemplar {q!r} duplicated across backends")
                seen[q] = backend
        self.assertGreaterEqual(len(seen), 24, "expected a meaningful exemplar pool")


if __name__ == "__main__":
    unittest.main()
