"""D008 PR2 — the production graph→vector cascade on the real stores. Owner: Brent.

PR1 (``test_d008_evals``) proved the exact-anchor gate against a *test-local*
reference; PR2 ports that gate into ``router.py`` as ``_GraphVectorCascade`` and a
profile-ready ``RouterConfig`` seam. These tests guard five contracts:

1. **Default-equivalence** — ``Router()`` and ``Router.with_config()`` (default
   config) produce identical ``classify`` / ``explain`` / ``route`` results, and
   ``explain`` keeps its exact ``{choice, scores, margin}`` shape. (The existing
   ``test_router`` / ``test_routing_evals`` suites pass unchanged alongside this.)
2. **Real cascade vs the PR1 fixture** — the SAME ``D008_CASES`` are imported (not
   duplicated); the real cascade's accept/fall-through must match each case's
   ``expected_gate`` with **zero false-accepts on the hard cases** (the PR1 blocker,
   now on production code).
3. **write() raises** — the route()-returned cascade is retrieval-only.
4. **as_of no-leak** — a future-timestamped anchor is never accepted and never
   leaks into results or the gate verdict; ``as_of`` flows into both stages.
5. **mutable-view regressions** — the cascade reflects live store contents (an
   anchor written after first use is still gated), ``route()`` rebinds the cascade
   when ``backends`` is replaced (no stale memoized view), and an unsupported
   ``gate`` value fails loud. (The two PR2-remediation correctness fixes.)

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_d008_cascade
"""

from __future__ import annotations

import unittest

from memeval.router import (
    ACCEPT,
    FALLTHROUGH,
    CascadeConfig,
    Router,
    RouterConfig,
    _GraphVectorCascade,
)
from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore
from memeval.stores.sqlite_store import SqliteVectorStore

# Import the PR1 case set + slice tags (do NOT duplicate the cases).
from memeval.stores.tests.test_d008_evals import (
    D008_CASES,
    SLICE_AS_OF,
    SLICE_EXACT,
    SLICE_HYDRATION,
)

GRAPH, VECTORS, MARKDOWN = "graph", "vectors", "markdown"
K = 5

# A spread of queries that exercises every backend route + the degenerate paths,
# so default-equivalence is checked across the whole decision surface, not one lane.
_EQUIV_QUERIES = [
    "what depends on schema.py",                 # -> graph
    "why did we choose keyword-only search",     # -> vectors
    "MemoryStore protocol",                      # -> markdown
    "what calls Router.route",                   # -> graph
    "summary of the OKF integration decision",   # -> vectors
    "DEFAULT_BUDGET_USD",                         # -> markdown
    "what breaks if I rename `UserRepository.findActive`?",  # -> graph
    "",                                          # degenerate -> semantic default
    "   ",                                        # whitespace -> semantic default
    "???!!! \U0001f525",                        # punctuation/emoji -> semantic default
]


def _build_stores(case):
    """Mirror PR1 ``_build_stores``: graph gets every item; vector honors ``vector_omit``."""
    graph = GraphStore()
    vector = SqliteVectorStore()  # :memory:, default offline _HashingEmbedder
    for item in case.items:
        graph.write(item)
        if item.item_id not in case.vector_omit:
            vector.write(item)
    return graph, vector


def _case(slice_id):
    """Return the (first) D008 case for a given slice id."""
    return next(c for c in D008_CASES if c.slice_id == slice_id)


def _as_cascade(store) -> _GraphVectorCascade:
    """Assert ``route()`` returned the cascade view and narrow the type for the gate seam."""
    assert isinstance(store, _GraphVectorCascade), "route() did not return the cascade view"
    return store


# --------------------------------------------------------------------------- #
# 1. Default-equivalence — Router() == Router.with_config(default)
# --------------------------------------------------------------------------- #
class DefaultEquivalenceTests(unittest.TestCase):
    def test_classify_and_explain_are_identical(self) -> None:
        base = Router()
        cfg = Router.with_config(config=RouterConfig())
        for q in _EQUIV_QUERIES:
            with self.subTest(query=q):
                self.assertEqual(base.classify(q), cfg.classify(q))
                self.assertEqual(base.explain(q), cfg.explain(q))

    def test_explain_shape_is_unchanged(self) -> None:
        # strict tests elsewhere depend on exactly these three keys for the default profile.
        for q in _EQUIV_QUERIES:
            with self.subTest(query=q):
                self.assertEqual(set(Router().explain(q)), {"choice", "scores", "margin"})

    def test_default_with_config_has_no_extra_explain_keys(self) -> None:
        self.assertEqual(
            set(Router.with_config().explain("what depends on schema.py")),
            {"choice", "scores", "margin"},
        )

    def test_route_returns_identical_backends(self) -> None:
        # SAME backend objects passed to both routers -> identity must match.
        backends = {GRAPH: GraphStore(), VECTORS: SqliteVectorStore(), MARKDOWN: GraphStore()}
        base = Router(backends)
        cfg = Router.with_config(backends, RouterConfig())
        try:
            for q in _EQUIV_QUERIES:
                with self.subTest(query=q):
                    self.assertIs(base.route(q), cfg.route(q))
        finally:
            backends[VECTORS].close()

    def test_default_profile_never_returns_a_cascade(self) -> None:
        # cascade off by default: a GRAPH query returns the plain graph backend.
        graph, vector = GraphStore(), SqliteVectorStore()
        try:
            r = Router.with_config({GRAPH: graph, VECTORS: vector}, RouterConfig())
            store = r.route("what depends on schema.py")
            self.assertIs(store, graph)
            self.assertNotIsInstance(store, _GraphVectorCascade)
        finally:
            vector.close()


# --------------------------------------------------------------------------- #
# 2. Real cascade vs the PR1 fixture (imported D008_CASES)
# --------------------------------------------------------------------------- #
class RealCascadeVsPR1Tests(unittest.TestCase):
    def _router(self, graph, vector):
        cfg = RouterConfig(cascade=CascadeConfig(enabled=True))
        return Router.with_config({GRAPH: graph, VECTORS: vector}, cfg)

    def test_every_case_routes_to_the_cascade(self) -> None:
        # every D008 case is GRAPH-classified, so with the cascade enabled route()
        # must return the cascade view (not a single backend).
        for case in D008_CASES:
            graph, vector = _build_stores(case)
            try:
                store = self._router(graph, vector).route(case.query)
                with self.subTest(case=case.name):
                    self.assertIsInstance(store, _GraphVectorCascade)
            finally:
                vector.close()

    def test_gate_decision_matches_expected_for_every_case(self) -> None:
        for case in D008_CASES:
            graph, vector = _build_stores(case)
            try:
                cascade = _as_cascade(self._router(graph, vector).route(case.query))
                verdict = cascade.gate(case.query, k=K, as_of=case.as_of)
                with self.subTest(case=case.name):
                    self.assertEqual(
                        verdict.decision, case.expected_gate,
                        f"{case.name}: gate reason={verdict.reason}")
            finally:
                vector.close()

    def test_zero_false_accepts_on_hard_cases(self) -> None:
        # THE blocker, now on production code: a hard fall-through case must never be
        # accepted, and the cascade must surface the vector recovery (not a graph decoy).
        offenders = []
        for case in (c for c in D008_CASES if c.hard):
            graph, vector = _build_stores(case)
            try:
                cascade = _as_cascade(self._router(graph, vector).route(case.query))
                verdict = cascade.gate(case.query, k=K, as_of=case.as_of)
                if verdict.decision != FALLTHROUGH:
                    offenders.append((case.name, verdict.reason))
                    continue
                got = [r.item_id for r in cascade.search(case.query, k=K, as_of=case.as_of)]
                want = [r.item_id for r in vector.search(case.query, k=K, as_of=case.as_of)]
                with self.subTest(case=case.name):
                    self.assertEqual(got, want,
                                     f"{case.name}: fall-through must return the vector result")
            finally:
                vector.close()
        self.assertEqual(offenders, [], f"false-accepts on hard cases: {offenders}")

    def test_accept_cases_project_gold_anchor_first(self) -> None:
        for case in (c for c in D008_CASES if c.expected_gate == ACCEPT):
            graph, vector = _build_stores(case)
            try:
                cascade = _as_cascade(self._router(graph, vector).route(case.query))
                results = cascade.search(case.query, k=K, as_of=case.as_of)
                ids = [r.item_id for r in results]
                with self.subTest(case=case.name):
                    self.assertTrue(results, f"{case.name}: accept must project >=1 item")
                    # exact seed (the unique anchor) is the graph rank-0 hit -> projected first.
                    verdict = cascade.gate(case.query, k=K, as_of=case.as_of)
                    self.assertEqual(ids[0], verdict.anchored_id,
                                     f"{case.name}: anchor must be projected first")
                    for gid in case.gold_item_ids:
                        self.assertIn(gid, ids, f"{case.name}: gold {gid!r} missing from projection")
                    # ranks are dense 0..n on the hydrated items.
                    self.assertEqual([r.rank for r in results], list(range(len(results))))
            finally:
                vector.close()

    def test_hydration_fallback_projects_missing_neighbor(self) -> None:
        # the missing-vector-hydration slice: a linked neighbor absent from the vector
        # store must still be projected via the graph fallback (not dropped).
        case = _case(SLICE_HYDRATION)
        graph, vector = _build_stores(case)
        try:
            cascade = self._router(graph, vector).route(case.query)
            ids = [r.item_id for r in cascade.search(case.query, k=K, as_of=case.as_of)]
            for omitted in case.vector_omit:
                self.assertIn(omitted, ids,
                              f"{omitted!r} omitted from vector must still hydrate from graph")
        finally:
            vector.close()


# --------------------------------------------------------------------------- #
# 3. write() raises — the cascade is a retrieval-only view
# --------------------------------------------------------------------------- #
class CascadeWriteRaisesTests(unittest.TestCase):
    def test_route_returned_cascade_write_raises(self) -> None:
        case = _case(SLICE_EXACT)
        graph, vector = _build_stores(case)
        try:
            cfg = RouterConfig(cascade=CascadeConfig(enabled=True))
            cascade = Router.with_config({GRAPH: graph, VECTORS: vector}, cfg).route(case.query)
            self.assertIsInstance(cascade, _GraphVectorCascade)
            with self.assertRaises(NotImplementedError):
                cascade.write(MemoryItem(item_id="x", content="nope"))
        finally:
            vector.close()


# --------------------------------------------------------------------------- #
# 4. as_of no-leak — a future anchor is never accepted or surfaced
# --------------------------------------------------------------------------- #
class AsOfNoLeakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = _case(SLICE_AS_OF)  # FutureService anchor at ts=5000, as_of=1000
        self.future_id = "future-service"

    def _cascade(self, graph, vector) -> _GraphVectorCascade:
        cfg = RouterConfig(cascade=CascadeConfig(enabled=True))
        router = Router.with_config({GRAPH: graph, VECTORS: vector}, cfg)
        return _as_cascade(router.route(self.case.query))

    def test_future_anchor_is_not_accepted_and_does_not_leak(self) -> None:
        graph, vector = _build_stores(self.case)
        try:
            cascade = self._cascade(graph, vector)
            verdict = cascade.gate(self.case.query, k=K, as_of=self.case.as_of)
            # not accepted, and the verdict must not name the future item.
            self.assertEqual(verdict.decision, FALLTHROUGH)
            self.assertNotEqual(verdict.anchored_id, self.future_id)
            ids = [r.item_id for r in cascade.search(self.case.query, k=K, as_of=self.case.as_of)]
            self.assertNotIn(self.future_id, ids, "future item must not leak into results")
        finally:
            vector.close()

    def test_as_of_flows_into_both_stages(self) -> None:
        # WITHOUT as_of the future anchor is visible (and accepted/surfaced); WITH the
        # case's as_of it disappears from results -> as_of reached both graph + vector.
        graph, vector = _build_stores(self.case)
        try:
            cascade = self._cascade(graph, vector)
            unbounded = [r.item_id for r in cascade.search(self.case.query, k=K, as_of=None)]
            bounded = [r.item_id for r in cascade.search(self.case.query, k=K, as_of=self.case.as_of)]
            self.assertIn(self.future_id, unbounded, "future item should be visible with no as_of")
            self.assertNotIn(self.future_id, bounded, "as_of must hide the future item")
        finally:
            vector.close()

    def test_resolution_ignores_future_anchor_even_if_graph_returns_it(self) -> None:
        # Directly exercise anchor resolution: with as_of below the anchor's timestamp,
        # the gate must not resolve to the future item (no-leak even if graph had a hit).
        graph, vector = _build_stores(self.case)
        try:
            cascade = self._cascade(graph, vector)
            anchored_id, _ = cascade._resolve_anchor(self.case.query, self.case.as_of)
            self.assertIsNone(anchored_id, "future anchor must not resolve under as_of")
            anchored_now, _ = cascade._resolve_anchor(self.case.query, None)
            self.assertEqual(anchored_now, self.future_id, "anchor resolves when not bounded")
        finally:
            vector.close()


# --------------------------------------------------------------------------- #
# 5. PR2 remediation regressions — the two BLOCKING correctness bugs the verifier
#    caught (stale anchor index; stale memoized cascade) + the unsupported-gate guard.
#    These exercise paths the original passing tests did not: writes after first use,
#    backend replacement after a route, and a non-"exact_anchor" gate value.
# --------------------------------------------------------------------------- #
def _okf_item(item_id, content, title) -> MemoryItem:
    """A MemoryItem carrying the OKF title the exact-anchor gate resolves against."""
    return MemoryItem(item_id=item_id, content=content, metadata={"okf_title": title})


class CascadeMutationRegressionTests(unittest.TestCase):
    """The cascade is a VIEW over mutable stores; it must reflect live store contents."""

    def test_anchor_index_reflects_writes_after_first_use(self) -> None:
        # BUG 1 (stale anchor index): build a cascade, USE it (which populated a
        # per-instance cached index in the buggy version), THEN write a new item to the
        # underlying graph+vector stores. The cascade must now resolve+accept the new
        # anchor — a cross-call cached index would never see it (gate wrongly falls through).
        graph, vector = GraphStore(), SqliteVectorStore()
        try:
            seed = _okf_item(
                "auth-guard",
                "AuthGuard; what depends on auth guard are the session checks",
                "AuthGuard")
            graph.write(seed)
            vector.write(seed)
            cascade = _GraphVectorCascade(graph, vector, CascadeConfig(enabled=True))

            # first use — accepts the seed anchor (and, in the buggy version, caches the index).
            first = cascade.gate('what depends on "auth-guard"', k=K)
            self.assertEqual(first.decision, ACCEPT, f"seed should accept (got {first.reason})")
            self.assertEqual(first.anchored_id, "auth-guard")

            # a NEW memory is written to the underlying mutable stores AFTER first use,
            # and is graph rank-0 for a query naming it.
            new = _okf_item(
                "widget-service",
                "WidgetService; what depends on widget service is the checkout cart and the widget",
                "WidgetService")
            graph.write(new)
            vector.write(new)

            # the cascade must reflect the live store contents — accept the new anchor.
            after = cascade.gate('what depends on "widget-service"', k=K)
            self.assertEqual(
                after.decision, ACCEPT,
                f"new anchor must be accepted after a write (got {after.reason}); "
                "a stale cross-call index would miss it and fall through")
            self.assertEqual(after.anchored_id, "widget-service")
            # and search projects the freshly-written anchor first.
            ids = [r.item_id for r in cascade.search('what depends on "widget-service"', k=K)]
            self.assertEqual(ids[0], "widget-service",
                             "freshly-written anchor must project first")
        finally:
            vector.close()


class CascadeBackendSwapRegressionTests(unittest.TestCase):
    """route() must bind the cascade to the CURRENT backends, not a stale memoized pair."""

    def _router(self, graph, vector) -> Router:
        cfg = RouterConfig(cascade=CascadeConfig(enabled=True))
        return Router.with_config({GRAPH: graph, VECTORS: vector}, cfg)

    def test_route_rebinds_cascade_after_backends_replaced(self) -> None:
        # BUG 2 (stale memoized cascade): route once to get a cascade bound to the
        # original stores, then REPLACE router.backends with new graph/vector stores.
        # A subsequent route() must return a cascade bound to the NEW stores (searching
        # the new data) — not a memoized view over the old backend objects.
        g1, v1 = GraphStore(), SqliteVectorStore()
        g2, v2 = GraphStore(), SqliteVectorStore()
        try:
            old = _okf_item(
                "alpha-service",
                "AlphaService; what depends on alpha service is the old pipeline",
                "AlphaService")
            g1.write(old)
            v1.write(old)
            router = self._router(g1, v1)
            c1 = _as_cascade(router.route('what depends on "alpha-service"'))
            self.assertIs(c1._graph, g1)

            # swap in entirely new backend objects holding different data.
            new = _okf_item(
                "beta-service",
                "BetaService; what depends on beta service is the new pipeline",
                "BetaService")
            g2.write(new)
            v2.write(new)
            router.backends = {GRAPH: g2, VECTORS: v2}

            c2 = _as_cascade(router.route('what depends on "beta-service"'))
            # bound to the NEW backend objects ...
            self.assertIsNot(c2, c1, "route() must not return the stale memoized cascade")
            self.assertIs(c2._graph, g2, "cascade must bind the swapped-in graph store")
            self.assertIs(c2._vector, v2, "cascade must bind the swapped-in vector store")
            # ... and searches the NEW data, never the swapped-out backends.
            ids = [r.item_id for r in c2.search('what depends on "beta-service"', k=K)]
            self.assertIn("beta-service", ids, "must search the new backends' data")
            self.assertNotIn("alpha-service", ids, "must not reach the swapped-out backends")
        finally:
            v1.close()
            v2.close()


class CascadeUnsupportedGateTests(unittest.TestCase):
    """An unsupported gate name must fail loud — only 'exact_anchor' ships in PR2."""

    def test_constructing_cascade_with_unknown_gate_raises(self) -> None:
        # NIT 3: CascadeConfig.gate was accepted but ignored. Using such a config in the
        # cascade must raise ValueError rather than silently behaving as exact-anchor.
        graph, vector = GraphStore(), SqliteVectorStore()
        try:
            with self.assertRaises(ValueError):
                _GraphVectorCascade(graph, vector, CascadeConfig(enabled=True, gate="nope"))
        finally:
            vector.close()

    def test_route_with_unknown_gate_raises(self) -> None:
        # the same guard holds on the production path: a profile naming an unsupported
        # gate fails loud when route() builds the cascade for a GRAPH-classified query.
        graph, vector = GraphStore(), SqliteVectorStore()
        try:
            cfg = RouterConfig(cascade=CascadeConfig(enabled=True, gate="fusion"))
            router = Router.with_config({GRAPH: graph, VECTORS: vector}, cfg)
            with self.assertRaises(ValueError):
                router.route("what depends on `PaymentService`")
        finally:
            vector.close()

    def test_default_exact_anchor_gate_constructs(self) -> None:
        # guard the guard: the supported gate must still construct cleanly.
        graph, vector = GraphStore(), SqliteVectorStore()
        try:
            cascade = _GraphVectorCascade(graph, vector, CascadeConfig(enabled=True))
            self.assertIsInstance(cascade, _GraphVectorCascade)
        finally:
            vector.close()


if __name__ == "__main__":
    unittest.main()
