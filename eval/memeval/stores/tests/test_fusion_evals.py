"""Cross-backend fusion eval (D025/PLAN-7) — the accuracy end of the speed↔accuracy spectrum. Owner: Brent.

Single-best routing (speed) is the CHEAP end; cross-backend FUSION is a first-class ACCURACY config:
fan a query out to several backends and merge their ranked results into one top-k. This eval (a) unit-
tests the fusion MECHANISM and (b) measures fusion against the existing speed/balanced(cascade) rows on
the durable ``D008_CASES`` fixture, head-to-head for the two merge methods, so the speed↔accuracy
tradeoff is legible and the method is chosen by DATA, not assertion:

* **rrf**   — Reciprocal Rank Fusion (rank-based; robust to per-backend score-scale differences).
* **score** — max-normalized score fusion.

Honest framing (measure-don't-assume): the fan-out returns the same top-k (so the retrieval-TOKEN budget
is unchanged — fusion buys recall at equal retrieval context, paying N× backend searches in compute).
Fusion is an OPT-IN profile (``fusion_profile()`` / ``RouterConfig(consult2=…)``); the offline default
stays single-route. What this eval actually shows:
  * the merge MECHANISM is correct (RRF/score merge + dedup + rank reset + fan-out), and
  * fusion's VALUE on COMPLEMENTARY backends is proven on a controlled split-gold case (canned stores:
    g1 only in A, g2 only in B → each single backend recall 0.5, fusion 1.0), and
  * on the real D008 fixture fusion is FLAT — D008 is graph-centric (gold is graph-resident; every query
    classifies to graph), so the cascade's exact-anchor gate already nails it and blind fusion only adds
    vector noise. Fusion is therefore NOT a universal win, and is NOT guaranteed to beat single-route:
    with a fixed final top-k, a shallow cross-backend item can displace a DEEP single-backend gold.
A real-store (markdown+graph+vector) complementarity fixture — the natural place fusion should win on
real data — is future work; this PR ships the mechanism + the honest D008 measurement + the opt-in profile.

Dual-mode (both from ``eval/``):
    python3 -m memeval.stores.tests.test_fusion_evals      # prints the matrix
    python3 -m unittest memeval.stores.tests.test_fusion_evals
"""

from __future__ import annotations

import unittest

from memeval.router import (
    Consult2Config,
    _FusionRetriever,
    fusion_profile,
    speed_profile,
)
from memeval.schema import MemoryItem, RetrievedItem
from memeval.stores.tests.test_d008_evals import D008_CASES, K
from memeval.stores.tests.test_profile_matrix import _BALANCED_CONFIG, run_profile


# --------------------------------------------------------------------------- #
# Mechanism: a canned backend with a fixed ranked list (deterministic merge tests).
# --------------------------------------------------------------------------- #
class _CannedStore:
    def __init__(self, ranked: list) -> None:
        self._ranked = ranked  # list[(item_id, content, score)]
        self.search_as_of = "unset"

    def search(self, query: str, *, k: int = 5, as_of=None, **kwargs):
        self.search_as_of = as_of
        return [RetrievedItem(item=MemoryItem(item_id=iid, content=c), score=s, rank=r)
                for r, (iid, c, s) in enumerate(self._ranked[:k])]

    def get(self, item_id: str):
        for iid, c, _ in self._ranked:
            if iid == item_id:
                return MemoryItem(item_id=iid, content=c)
        return None

    def all(self):
        return [MemoryItem(item_id=iid, content=c) for iid, c, _ in self._ranked]

    def write(self, item):  # pragma: no cover
        pass


def _fusion(stores: dict, **cfg) -> _FusionRetriever:
    return _FusionRetriever(stores, Consult2Config(enabled=True, **cfg))


class FusionMechanismTests(unittest.TestCase):
    def test_rrf_merges_and_dedups(self) -> None:
        # 'y' appears in BOTH backends -> its summed RRF beats items in only one.
        a = _CannedStore([("x", "doc x", 0.9), ("y", "doc y", 0.4)])
        b = _CannedStore([("y", "doc y", 0.8), ("z", "doc z", 0.3)])
        fused = _fusion({"a": a, "b": b}, method="rrf").search("q", k=5)
        ids = [h.item_id for h in fused]
        self.assertEqual(ids[0], "y", "item in both backends must fuse to the top")
        self.assertEqual(set(ids), {"x", "y", "z"}, "merged + de-duplicated across backends")
        self.assertEqual([h.rank for h in fused], list(range(len(fused))), "ranks reset 0..n")
        self.assertEqual(ids.count("y"), 1, "no duplicate of the cross-backend item")

    def test_score_fusion_merges(self) -> None:
        a = _CannedStore([("x", "doc x", 1.0), ("y", "doc y", 0.5)])
        b = _CannedStore([("y", "doc y", 1.0)])
        fused = _fusion({"a": a, "b": b}, method="score").search("q", k=5)
        # y: 0.5(norm in a) + 1.0(norm in b) = 1.5; x: 1.0 -> y first.
        self.assertEqual([h.item_id for h in fused][0], "y")

    def test_k_truncates_and_non_positive_returns_empty(self) -> None:
        a = _CannedStore([("x", "x", 0.9), ("y", "y", 0.8), ("z", "z", 0.7)])
        f = _fusion({"a": a}, method="rrf")
        self.assertEqual(len(f.search("q", k=2)), 2)
        self.assertEqual(f.search("q", k=0), [])
        self.assertEqual(f.search("q", k=-1), [])

    def test_as_of_is_forwarded_to_backends(self) -> None:
        a = _CannedStore([("x", "x", 0.9)])
        _fusion({"a": a}, method="rrf").search("q", k=3, as_of=123.0)
        self.assertEqual(a.search_as_of, 123.0)

    def test_backends_subset_selects_fan_out(self) -> None:
        a = _CannedStore([("x", "x", 0.9)])
        b = _CannedStore([("y", "y", 0.9)])
        fused = _fusion({"a": a, "b": b}, method="rrf", backends=("a",)).search("q", k=5)
        self.assertEqual({h.item_id for h in fused}, {"x"}, "only the selected backend is fused")

    def test_write_raises_and_bad_method_raises(self) -> None:
        with self.assertRaises(NotImplementedError):
            _fusion({"a": _CannedStore([])}, method="rrf").write(MemoryItem(item_id="x", content="y"))
        with self.assertRaises(ValueError):
            _FusionRetriever({}, Consult2Config(enabled=True, method="bogus"))

    def test_recovers_split_gold_across_complementary_backends(self) -> None:
        # THE value of fusion: when gold is SPLIT across backends (g1 only in A, g2 only in B), fusion
        # recovers BOTH while either single backend recovers only half. This is the controlled
        # complementarity proof; the D008 fixture is graph-centric and does NOT exercise it (see the
        # matrix report — fusion is flat there because the gold is graph-resident).
        a = _CannedStore([("g1", "gold one", 0.9), ("n1", "noise a", 0.3)])
        b = _CannedStore([("g2", "gold two", 0.9), ("n2", "noise b", 0.3)])
        gold = {"g1", "g2"}

        def _recall(hits):
            return len(gold & {h.item_id for h in hits}) / len(gold)

        self.assertEqual(_recall(a.search("q", k=5)), 0.5, "backend A alone recovers half")
        self.assertEqual(_recall(b.search("q", k=5)), 0.5, "backend B alone recovers half")
        for method in ("rrf", "score"):
            fused = _fusion({"a": a, "b": b}, method=method).search("q", k=5)
            self.assertEqual(_recall(fused), 1.0,
                             f"{method} fusion must recover gold split across complementary backends")


# --------------------------------------------------------------------------- #
# Measurement: fusion vs speed/balanced on the durable D008 fixture.
# --------------------------------------------------------------------------- #
def build_fusion_matrix() -> dict:
    """Run speed / balanced(cascade) / rrf-fusion / score-fusion over D008_CASES."""
    return {
        "speed": run_profile(speed_profile()),
        "balanced": run_profile(_BALANCED_CONFIG),
        "rrf": run_profile(fusion_profile(method="rrf")),
        "score": run_profile(fusion_profile(method="score")),
    }


def _winner(matrix: dict) -> str:
    """The better fusion method by recall, MRR as tiebreak."""
    rrf, score = matrix["rrf"], matrix["score"]
    return "rrf" if (rrf["recall"], rrf["mrr"]) >= (score["recall"], score["mrr"]) else "score"


class FusionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = build_fusion_matrix()

    def test_both_fusion_methods_run(self) -> None:
        for name in ("rrf", "score"):
            self.assertTrue(self.m[name]["rows"], f"{name} fusion produced no rows")
            self.assertGreater(self.m[name]["n_graded"], 0)

    def test_fusion_is_flat_on_graph_centric_d008(self) -> None:
        # HONEST finding (measure-don't-assume, à la D022/D024): D008 is graph-centric — every query
        # classifies to graph and the gold is graph-resident, so the cascade's exact-anchor gate already
        # nails it and blind fusion adds only vector noise. MEASURED on D008: fusion recall == speed
        # recall (flat) and fusion MRR ≤ the gated cascade. Fusion's value needs COMPLEMENTARY backends
        # (proven in test_recovers_split_gold_across_complementary_backends), which D008 doesn't exercise.
        #
        # NOTE — this is a fixture-specific MEASUREMENT, not a general invariant: fusion is NOT guaranteed
        # ≥ speed recall in general, because with a fixed final top-k a shallow cross-backend item can
        # displace a DEEP single-backend gold. We assert what actually holds on D008, and document the
        # tradeoff rather than claiming a guarantee. Guards against overselling fusion as a universal win.
        speed = self.m["speed"]["recall"]
        for name in ("rrf", "score"):
            self.assertAlmostEqual(self.m[name]["recall"], speed,
                                   msg=f"{name} fusion is FLAT on graph-centric D008 (measured)")
        winner = self.m[_winner(self.m)]
        self.assertLessEqual(round(winner["mrr"], 6), round(self.m["balanced"]["mrr"], 6) + 1e-9,
                             "the gated cascade's MRR is ≥ blind fusion's on graph-centric data")


def _report() -> None:
    m = build_fusion_matrix()
    n = len(D008_CASES)
    print(f"FUSION MATRIX — speed↔accuracy over {n} D008 cases (K={K}); graph+vector, offline embedder.\n")
    hdr = f"{'profile':<12} {'route':<16} {'recall@'+str(K):<9} {'MRR':<7} {'mem-tok':<8} {'n_graded':<8}"
    print(hdr)
    print("-" * len(hdr))
    rows = [
        ("speed", "single→graph", m["speed"]),
        ("balanced", "graph→vector", m["balanced"]),
        ("fusion:rrf", "fan-out⊕rrf", m["rrf"]),
        ("fusion:score", "fan-out⊕score", m["score"]),
    ]
    for label, route, r in rows:
        print(f"{label:<12} {route:<16} {r['recall']:<9.3f} {r['mrr']:<7.3f} "
              f"{r['tokens']:<8} {r['n_graded']:<8}")
    win = _winner(m)
    print(f"\nHead-to-head fusion winner (recall, then MRR): {win}")
    print(f"recall: rrf={m['rrf']['recall']:.3f}  score={m['score']['recall']:.3f}  "
          f"| speed={m['speed']['recall']:.3f}  balanced={m['balanced']['recall']:.3f}")
    print("Note: fusion returns the same top-k as single-route, so mem-tok (retrieval context) is "
          "comparable — fusion buys recall at equal token budget, paying N× backend searches in compute.")


if __name__ == "__main__":
    _report()
