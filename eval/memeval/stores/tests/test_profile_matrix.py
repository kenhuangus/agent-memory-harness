"""D008 PR2.5 — the profile-matrix eval reporter. Owner: Brent.

The committed, tested version of the throwaway cascade preview: it runs each
runnable routing *profile* over the durable ``D008_CASES`` fixture and prints the
speed↔accuracy/cascade tradeoff curve — one row per profile, columns = the metrics
that make the tradeoff legible (recall@K, MRR, fall-through recovery@K, gate-decision
accuracy, false-accepts, and total memory-tokens returned, the efficiency axis).

Profiles in the matrix:

* **speed** — ``speed_profile()`` == ``RouterConfig()``: today's router, single best
  route per query, cascade OFF, no gate. Every D008 query is GRAPH-classified, so
  speed routes them all to the graph store (this is the graph-only single-route
  baseline, surfaced as a profile).
* **balanced** — ``RouterConfig(cascade=CascadeConfig(enabled=True))``: the real
  graph→vector cascade on the stdlib stores. NOT a public preset (D016 ruling —
  balanced is a reporter ROW only; promote to a named preset later only if the data
  justifies it).
* **accuracy** — ``accuracy_profile(classifier=…, embed=…)``: injected classifier +
  real embedder + cascade ON. The heavy strategies are caller-injected (PR3), so this
  row is shown as "requires injected classifier+embed (PR3)" and is only *run* when a
  caller supplies BOTH (set :data:`INJECTED_CLASSIFIER` AND :data:`INJECTED_EMBED`).
  When it runs, the injected ``embed`` is wired into that profile's vector store, so an
  injected accuracy run genuinely uses the injected embedder — no offline-embedder run
  is ever labeled "accuracy". PR3 supplies the real classifier + embedder.

The single-route graph-only / vector-only baselines are reused verbatim from
``test_d008_evals.score()`` so the numbers stay consistent with the PR1 baseline.

Dual-mode (both work from ``eval/``):
    python3 -m memeval.stores.tests.test_profile_matrix      # prints the matrix
    python3 -m unittest memeval.stores.tests.test_profile_matrix

The tests assert the invariants that MUST hold (not brittle exact numbers): the presets
are shaped correctly, the matrix runs with the speed + balanced rows, and the SAFETY
contract on the balanced/cascade profile — 0 false-accepts on hard cases and 100%
gate-decision accuracy over the fixture (the silent-wrong-success defense) — plus the
defensible best-of-both bounds (cascade recall ≥ vector-only, cascade MRR ≥ graph-only).
"""

from __future__ import annotations

import unittest

from memeval.router import (
    CascadeConfig,
    Router,
    RouterConfig,
    RuleBasedClassifier,
    accuracy_local_profile,
    accuracy_profile,
    speed_profile,
    GRAPH,
    VECTORS,
    _GraphVectorCascade,
)

# Import the durable fixture + shared metric helpers (do NOT duplicate them) so the
# numbers are computed exactly the way PR1's baseline computes them.
from memeval.stores.tests.test_d008_evals import (
    ACCEPT,
    D008_CASES,
    FALLTHROUGH,
    K,
    _build_stores,
    _mrr,
    _recall_at_k,
    score,
)

# Stores for the INJECTED accuracy run: its vector store is built around the injected
# embedder (not the offline ``_HashingEmbedder``), so an injected accuracy run genuinely
# exercises the injected embedder. ``_HashingEmbedder`` is imported only to back the test
# spy below — the speed/balanced rows keep using ``_build_stores`` (offline) unchanged.
from memeval.stores.graph_store import GraphStore
from memeval.stores.sqlite_store import SqliteVectorStore, _HashingEmbedder

# Forward-compatible injection seam for the accuracy row. Left None: the accuracy
# profile is shown in the matrix but NOT run (its real classifier + embedder land in
# PR3). A caller can set BOTH to run the accuracy row — the injected embedder is then
# wired into that profile's vector store, so the run genuinely uses it. Setting only one
# (or neither) leaves the row a placeholder; an offline-embedder run is never labeled
# "accuracy".
INJECTED_CLASSIFIER = None
INJECTED_EMBED = None

# The balanced profile is a reporter config ONLY (D016 ruling — not a public preset).
_BALANCED_CONFIG = RouterConfig(profile_name="balanced",
                                cascade=CascadeConfig(enabled=True))


def _build_stores_with_embed(case, embed):
    """Like ``test_d008_evals._build_stores`` but builds the vector store AROUND an
    injected ``embed`` (the accuracy profile's real-embedder seam) instead of the
    offline ``_HashingEmbedder``. Used only by the injected accuracy run so its vector
    stage genuinely exercises the injected embedder — the speed/balanced rows keep using
    the offline ``_build_stores`` unchanged.
    """
    graph = GraphStore()
    vector = SqliteVectorStore(embed=embed)
    for item in case.items:
        graph.write(item)
        if item.item_id not in case.vector_omit:
            vector.write(item)
    return graph, vector


# --------------------------------------------------------------------------- #
# One profile, driven over every D008 case (mirrors the cascade preview).
# --------------------------------------------------------------------------- #
def run_profile(config: RouterConfig, *, embed=None) -> dict:
    """Route every ``D008_CASES`` query through ``config`` and aggregate the metrics.

    Builds fresh stores per case, routes with the profile's config, then searches. The
    vector store uses the offline ``_HashingEmbedder`` by default; when ``embed`` is
    supplied (the injected accuracy run) the vector store is built AROUND that injected
    embedder, so the run genuinely exercises it rather than the offline path. recall@K /
    MRR / tokens are collected uniformly for every profile. Gate-decision accuracy,
    false-accepts and fall-through recovery@K are collected ONLY when the routed store is
    the cascade view (a single-route profile has no gate — those columns report ``None``).
    """
    rows = []
    recalls, mrrs = [], []
    total_tokens = 0
    has_gate = False
    gate_correct = 0
    fa_hard = 0
    fa_any = 0
    n_recoverable = 0
    n_recovered = 0
    for case in D008_CASES:
        graph, vector = (_build_stores_with_embed(case, embed) if embed is not None
                         else _build_stores(case))
        try:
            store = Router.with_config({GRAPH: graph, VECTORS: vector}, config).route(case.query)
            is_cascade = isinstance(store, _GraphVectorCascade)
            has_gate = has_gate or is_cascade
            decision = (store.gate(case.query, k=K, as_of=case.as_of).decision
                        if is_cascade else None)
            hits = store.search(case.query, k=K, as_of=case.as_of)
            ids = [h.item_id for h in hits]
            tokens = sum(h.tokens for h in hits)
            total_tokens += tokens
            gold = case.gold_item_ids
            rec = _recall_at_k(ids, gold) if gold else None
            if gold:
                recalls.append(rec)
                mrrs.append(_mrr(ids, gold))
            if is_cascade:
                if decision == case.expected_gate:
                    gate_correct += 1
                if case.expected_gate == FALLTHROUGH and decision == ACCEPT:
                    fa_any += 1
                if case.hard and decision == ACCEPT:
                    fa_hard += 1
                # fall-through recovery@K: a fall-through case carrying gold whose
                # (vector) result recovers >=1 gold id.
                if decision == FALLTHROUGH and gold:
                    n_recoverable += 1
                    if set(gold) & set(ids):
                        n_recovered += 1
            rows.append({
                "name": case.name, "hard": case.hard, "decision": decision,
                "ids": ids, "gold": list(gold), "recall": rec, "tokens": tokens,
            })
        finally:
            vector.close()

    def _mean(vals):
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "rows": rows,
        "has_gate": has_gate,
        "recall": _mean(recalls),
        "mrr": _mean(mrrs),
        "n_graded": len(recalls),
        "tokens": total_tokens,
        # gate-only columns: None for a single-route profile (no gate stage).
        "gate_acc": (gate_correct / len(D008_CASES)) if has_gate else None,
        "fa_hard": fa_hard if has_gate else None,
        "fa_any": fa_any if has_gate else None,
        "recovery": (n_recovered / n_recoverable if n_recoverable else 0.0) if has_gate else None,
        "n_recoverable": n_recoverable if has_gate else None,
    }


def build_matrix(*, classifier=None, embed=None) -> dict:
    """Run every runnable profile + collect the single-route baselines from ``score()``.

    ``accuracy`` is run ONLY when BOTH an injected ``classifier`` AND ``embed`` are
    supplied (PR3); the injected ``embed`` is wired into that profile's vector store so
    the run genuinely uses it. With only one (or neither) it stays a placeholder row
    (``None``) — so an offline-embedder run is never labeled "accuracy". Returns a dict
    the reporter prints and the tests assert against.
    """
    return {
        "speed": run_profile(speed_profile()),
        "balanced": run_profile(_BALANCED_CONFIG),
        # BOTH the classifier AND the embedder must be injected to run accuracy; the
        # injected embed is wired into the profile's vector store (see run_profile).
        "accuracy": (run_profile(accuracy_profile(classifier=classifier, embed=embed), embed=embed)
                     if classifier is not None and embed is not None else None),
        "baselines": score(),  # graph-only / vector-only recall+MRR, consistent w/ PR1
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _fmt(value, width, spec="", na="n/a") -> str:
    """Format a metric cell, rendering None as ``na`` (a column the profile lacks)."""
    if value is None:
        return f"{na:<{width}}"
    return f"{format(value, spec):<{width}}"


def _report() -> None:
    matrix = build_matrix(classifier=INJECTED_CLASSIFIER, embed=INJECTED_EMBED)
    base = matrix["baselines"]
    n = len(D008_CASES)

    print(f"D008 PROFILE MATRIX — speed↔accuracy/cascade tradeoff over {n} cases (K={K})")
    print("Profiles routed over GraphStore + SqliteVectorStore (offline _HashingEmbedder, "
          "stdlib only).")
    print("Rows = routing profiles; columns = the tradeoff axes. recov/gate/FA apply only "
          "to gated (cascade) profiles.\n")

    hdr = (f"{'profile':<11} {'route':<14} {'recall@'+str(K):<9} {'MRR':<7} "
           f"{'recov@'+str(K):<8} {'gate-acc':<9} {'FA(hard)':<9} {'FA(any)':<8} {'mem-tok':<7}")
    print(hdr)
    print("-" * len(hdr))

    def _print_profile(label: str, route: str, m: dict) -> None:
        print(f"{label:<11} {route:<14} "
              f"{_fmt(m['recall'], 9, '.3f')} {_fmt(m['mrr'], 7, '.3f')} "
              f"{_fmt(m['recovery'], 8, '.0%')} {_fmt(m['gate_acc'], 9, '.0%')} "
              f"{_fmt(m['fa_hard'], 9, 'd')} {_fmt(m['fa_any'], 8, 'd')} "
              f"{_fmt(m['tokens'], 7, 'd')}")

    _print_profile("speed", "single→graph", matrix["speed"])
    _print_profile("balanced", "graph→vector", matrix["balanced"])
    if matrix["accuracy"] is not None:
        _print_profile("accuracy", "graph→vector*", matrix["accuracy"])
    else:
        print(f"{'accuracy':<11} {'graph→vector*':<14} "
              "requires injected classifier+embed (PR3)")

    print("\nReference single-route baselines (from test_d008_evals.score(), consistent w/ PR1):")
    print(f"  {'graph-only':<12} recall@{K}={base['graph_recall_at_k']:.3f}  "
          f"MRR={base['graph_mrr']:.3f}")
    print(f"  {'vector-only':<12} recall@{K}={base['vector_recall_at_k']:.3f}  "
          f"MRR={base['vector_mrr']:.3f}")
    print(f"  (over {len(base['graded'])} graded cases carrying gold ids)")

    # Per-case routing for the balanced/cascade profile (the interesting story:
    # accepts project graph+links; hard cases fall through to the vector recovery).
    print("\nBalanced (cascade) per-case routing:")
    print(f"  {'case':<30} {'route':<14} {'hard':<5} {'recall':<7} {'tok':<4} returned -> gold")
    print("  " + "-" * 96)
    for r in matrix["balanced"]["rows"]:
        route = ("graph-accept" if r["decision"] == ACCEPT
                 else "vec-fallthru" if r["decision"] == FALLTHROUGH else str(r["decision"]))
        rec = "n/a" if r["recall"] is None else f"{r['recall']:.2f}"
        hard = "HARD" if r["hard"] else ""
        print(f"  {r['name']:<30} {route:<14} {hard:<5} {rec:<7} {r['tokens']:<4} "
              f"{r['ids']} -> {r['gold']}")

    bal = matrix["balanced"]
    print(f"\nBalanced safety: false-accepts on hard cases = {bal['fa_hard']} (MUST be 0)  |  "
          f"gate-decision accuracy = {bal['gate_acc']:.0%} (MUST be 100%)")
    print(f"Best-of-both: cascade recall@{K}={bal['recall']:.3f} ≥ vector-only "
          f"{base['vector_recall_at_k']:.3f}  AND  cascade MRR={bal['mrr']:.3f} ≥ graph-only "
          f"{base['graph_mrr']:.3f}")
    print(f"Efficiency axis: balanced memory-tokens returned (sum across {n} cases) = "
          f"{bal['tokens']}  (single best-route per query, no fan-out)")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class ProfilePresetContractTests(unittest.TestCase):
    """The preset factories build the configs PR3 builds against."""

    def test_speed_profile_equals_default_config(self) -> None:
        # speed is the bare default, named — frozen-dataclass equality holds.
        self.assertEqual(speed_profile(), RouterConfig())

    def test_speed_profile_leaves_cascade_off(self) -> None:
        self.assertFalse(speed_profile().cascade.enabled)
        self.assertIsNone(speed_profile().classifier)

    def test_accuracy_profile_enables_cascade_and_carries_strategies(self) -> None:
        sentinel_embed = object()
        cfg = accuracy_profile(
            classifier=RuleBasedClassifier(RouterConfig()),
            embed=sentinel_embed,
            embed_model="some-model",
            k=8,
        )
        self.assertTrue(cfg.cascade.enabled, "accuracy_profile must enable the cascade")
        self.assertIsNotNone(cfg.classifier, "accuracy_profile carries the injected classifier")
        self.assertIs(cfg.embed, sentinel_embed, "accuracy_profile carries the injected embedder")
        self.assertEqual(cfg.embed_model, "some-model")
        self.assertEqual(cfg.k, 8)
        self.assertEqual(cfg.profile_name, "accuracy")
        # consult2 is left at its declared default — no RRF ships in PR2.5.
        self.assertFalse(cfg.consult2.enabled)

    def test_accuracy_local_profile_has_distinct_name(self) -> None:
        sentinel_embed = object()
        cfg = accuracy_local_profile(
            classifier=RuleBasedClassifier(RouterConfig()),
            embed=sentinel_embed,
            embed_model="sentence-transformers/all-MiniLM-L6-v2",
        )
        self.assertTrue(cfg.cascade.enabled)
        self.assertIs(cfg.embed, sentinel_embed)
        self.assertEqual(cfg.profile_name, "accuracy-local")

    def test_factories_are_exported(self) -> None:
        import memeval.router as router_mod
        self.assertIn("speed_profile", router_mod.__all__)
        self.assertIn("accuracy_profile", router_mod.__all__)
        self.assertIn("accuracy_local_profile", router_mod.__all__)


class ProfileMatrixShapeTests(unittest.TestCase):
    """The matrix runs and contains the runnable profiles."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = build_matrix(classifier=INJECTED_CLASSIFIER, embed=INJECTED_EMBED)

    def test_matrix_contains_speed_and_balanced_rows(self) -> None:
        self.assertIn("speed", self.matrix)
        self.assertIn("balanced", self.matrix)
        self.assertTrue(self.matrix["speed"]["rows"], "speed profile produced no rows")
        self.assertTrue(self.matrix["balanced"]["rows"], "balanced profile produced no rows")

    def test_speed_is_single_route_balanced_is_gated(self) -> None:
        # speed has no gate stage; balanced routes through the cascade gate.
        self.assertFalse(self.matrix["speed"]["has_gate"])
        self.assertTrue(self.matrix["balanced"]["has_gate"])

    def test_speed_recall_matches_graph_only_baseline(self) -> None:
        # consistency check: every D008 query is GRAPH-classified, so speed routes
        # them all to graph -> its recall is exactly the graph-only baseline.
        self.assertAlmostEqual(self.matrix["speed"]["recall"],
                               self.matrix["baselines"]["graph_recall_at_k"])
        self.assertAlmostEqual(self.matrix["speed"]["mrr"],
                               self.matrix["baselines"]["graph_mrr"])


class _SpyEmbedder:
    """Records that it was called, then delegates to the offline embedder for a valid
    vector. Lets a test prove the injected embedder is genuinely wired into the accuracy
    run's vector store (and not silently replaced by the offline default).
    """

    def __init__(self) -> None:
        self.calls = 0
        self._inner = _HashingEmbedder()

    def __call__(self, text):
        self.calls += 1
        return self._inner(text)


class AccuracyRowHonestyTests(unittest.TestCase):
    """The accuracy row must never present a half-wired run as "accuracy" (PR2.5 honesty).

    PR2.5 ships no real classifier/embedder. The accuracy row is a forward-compat hook:
    a placeholder UNLESS the caller injects BOTH a classifier AND an embedder; and when
    it runs, the cascade's vector store is genuinely built around the INJECTED embedder
    (never the offline ``_HashingEmbedder`` hiding behind an "accuracy" label). PR3
    supplies the real strategies.
    """

    def test_placeholder_unless_both_classifier_and_embed(self) -> None:
        # neither, or only one, injected -> placeholder (no row computed, so no
        # offline-embedder run is ever mislabeled "accuracy").
        self.assertIsNone(build_matrix()["accuracy"])
        self.assertIsNone(
            build_matrix(classifier=RuleBasedClassifier(RouterConfig()))["accuracy"],
            "a classifier alone must NOT run accuracy (its embedder would be offline)")
        self.assertIsNone(
            build_matrix(embed=_HashingEmbedder())["accuracy"],
            "an embedder alone must NOT run accuracy (no injected classifier)")

    def test_injected_run_uses_the_injected_embedder(self) -> None:
        spy = _SpyEmbedder()
        m = build_matrix(classifier=RuleBasedClassifier(RouterConfig()), embed=spy)
        acc = m["accuracy"]
        self.assertIsNotNone(acc, "accuracy must run when BOTH strategies are injected")
        self.assertTrue(acc["rows"], "injected accuracy run produced no rows")
        self.assertTrue(acc["has_gate"], "accuracy enables the cascade -> it has a gate")
        # THE honesty assertion: the injected embedder was actually exercised by the
        # accuracy run's vector store — proving no offline embedder hid behind the label.
        self.assertGreater(
            spy.calls, 0,
            "the injected embedder must be wired into the accuracy run's vector store")


class BalancedSafetyTests(unittest.TestCase):
    """The SAFETY contract on the balanced/cascade profile (the hard blocker)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bal = build_matrix()["balanced"]

    def test_zero_false_accepts_on_hard_cases(self) -> None:
        # THE blocker: the cascade must never accept a hard fall-through case
        # (the silent-wrong-success defense).
        self.assertEqual(self.bal["fa_hard"], 0, "balanced must have 0 hard false-accepts")

    def test_no_false_accepts_anywhere(self) -> None:
        self.assertEqual(self.bal["fa_any"], 0,
                         "balanced must not accept any fall-through case")

    def test_gate_decision_accuracy_is_100pct(self) -> None:
        self.assertEqual(self.bal["gate_acc"], 1.0,
                         "balanced gate decisions must match every case's expected_gate")


class BalancedBestOfBothTests(unittest.TestCase):
    """Defensible best-of-both bounds: the cascade routes accepts to graph and
    fall-throughs to vector, so it inherits vector's recall floor and graph's MRR
    floor on this fixture."""

    @classmethod
    def setUpClass(cls) -> None:
        m = build_matrix()
        cls.bal = m["balanced"]
        cls.base = m["baselines"]

    def test_cascade_recall_ge_vector_only(self) -> None:
        self.assertGreaterEqual(self.bal["recall"], self.base["vector_recall_at_k"],
                                "cascade recall@K should be >= vector-only recall@K")

    def test_cascade_mrr_ge_graph_only(self) -> None:
        self.assertGreaterEqual(self.bal["mrr"], self.base["graph_mrr"],
                                "cascade MRR should be >= graph-only MRR")


if __name__ == "__main__":
    _report()
