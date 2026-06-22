"""Fusion bake-off (D027/D028) — does cross-backend FUSION beat the best single backend, and is it
RRF or score? Owner: Brent.

Fusion was measured FLAT on the graph-centric D008 fixture and could not be decided offline at all: the
three backends are co-lexical (markdown BM25, vectors char-trigram, graph token-seed all key off shared
tokens), so they agree and fusion adds nothing — see DECISION_LOG D027 + the calibration in
``work/calibrate_fusion_complementarity.py``. The genuine cross-backend complementarity is a
**real-embedder** phenomenon (the D019/D020 lesson): a real semantic embedder makes the *vectors*
backend recover divergence gold the lexical markdown/graph cannot, while markdown/graph still win the
lexical *control* queries — so across a MIXED workload no single backend wins everything and fusion can.

This bake-off reuses the D019 semantic haystack (34 memories; 15 semantic-divergence cases where the
gold shares ~no surface form with the query + 5 lexical controls) and scores, per case, the recall@k of
markdown / vectors / graph alone vs RRF-fusion vs score-fusion across all three.

**OFFLINE (committed, CI-safe) — and a finding the calibration surfaced:** the D019 "divergence" cases
were calibrated to defeat the char-trigram *hashing* embedder, so offline the **vectors** backend gets
**0** divergence recall (the headroom). But **markdown (BM25) is a different lexical mechanism with
different blind spots and recovers ~half of those divergence cases offline** (graph too) — "divergence"
is hashing-specific, not universally lexically-hard. The committed point that matters: **offline,
score-fusion TIES the best single backend (markdown, 0.650) and RRF TRAILS it (0.600) — neither beats
it**, so there is no offline fusion win to oversell. The real question — does adding a *real* semantic
embedder to the vectors backend let fusion beat markdown-alone (and is RRF or score better)? — is
**captained**: run from ``work/fusion_bakeoff_live.py`` with a live key, recorded in DECISION_LOG D028.
The committed path makes **no network call** — the live sanity below requires an explicit
``MEMEVAL_LIVE=1`` opt-in (not just a key), so CI never calls out even if a key is present.

Reproduce the offline report: cd eval && python3 -m memeval.stores.tests.test_fusion_bakeoff
Run the guard:               cd eval && python3 -m unittest memeval.stores.tests.test_fusion_bakeoff
"""

from __future__ import annotations

import os
import tempfile
import unittest

from memeval.router import GRAPH, MARKDOWN, VECTORS, Consult2Config, _FusionRetriever
from memeval.stores.graph_store import GraphStore
from memeval.stores.markdown_store import MarkdownStore
from memeval.stores.sqlite_store import SqliteVectorStore
from memeval.stores.tests.test_semantic_retrieval_evals import (
    CONTROL,
    CORPUS,
    DIVERGENCE,
    K,
    SEMANTIC_CASES,
    _recall_at_k,
)

_SINGLE = (MARKDOWN, VECTORS, GRAPH)
_FUSION = ("fusion_rrf", "fusion_score")


def _build_backends(tmp: str, embed=None) -> dict:
    """markdown + vectors(embed) + graph over the whole D019 corpus (needle-in-haystack)."""
    backends = {
        MARKDOWN: MarkdownStore(os.path.join(tmp, "md")),
        VECTORS: SqliteVectorStore(embed=embed),
        GRAPH: GraphStore(),
    }
    for item in CORPUS:
        for store in backends.values():
            store.write(item)
    return backends


def score_fusion_bakeoff(embed=None, *, k: int = K) -> dict:
    """Per-case recall@k for each single backend + RRF/score fusion; aggregated by kind.

    ``embed=None`` -> offline hashing (committed headroom path). The captained run passes a real
    ``VoyageEmbedder`` (``work/fusion_bakeoff_live.py``).
    """
    with tempfile.TemporaryDirectory() as tmp:
        backends = _build_backends(tmp, embed)
        try:
            rows = []
            for case in SEMANTIC_CASES:
                row = {"name": case.name, "kind": case.kind}
                for name, store in backends.items():
                    ids = [h.item_id for h in store.search(case.query, k=k)]
                    row[name] = _recall_at_k(ids, case.gold_item_ids)
                for method in ("rrf", "score"):
                    fr = _FusionRetriever(backends, Consult2Config(enabled=True, method=method))
                    ids = [h.item_id for h in fr.search(case.query, k=k)]
                    row[f"fusion_{method}"] = _recall_at_k(ids, case.gold_item_ids)
                rows.append(row)
        finally:
            backends[VECTORS].close()

    cols = list(_SINGLE) + list(_FUSION)

    def _mean(kind, col):
        sel = [r for r in rows if (kind is None or r["kind"] == kind)]
        return sum(r[col] for r in sel) / len(sel) if sel else 0.0

    agg = {scope: {c: _mean(kind, c) for c in cols}
           for scope, kind in (("overall", None), ("divergence", DIVERGENCE), ("control", CONTROL))}
    return {"rows": rows, "agg": agg, "cols": cols,
            "best_single_overall": max(agg["overall"][c] for c in _SINGLE)}


# --------------------------------------------------------------------------- #
# OFFLINE committed assertions — headroom only (lexical can't do semantic)
# --------------------------------------------------------------------------- #
class FusionBakeoffOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.s = score_fusion_bakeoff()  # offline hashing embedder

    def test_offline_hashing_vectors_miss_all_divergence(self) -> None:
        # The D019 headroom fact: the char-trigram HASHING embedder gets 0 divergence recall — the room
        # a real embedder must recover in the captained run. (markdown/graph are a DIFFERENT mechanism.)
        self.assertEqual(self.s["agg"]["divergence"][VECTORS], 0.0,
                         "offline hashing vectors must miss all divergence gold (the headroom)")

    def test_offline_lexical_backends_partially_recover_divergence(self) -> None:
        # The calibration finding: "divergence" defeats the hashing embedder, NOT markdown's BM25 / graph
        # — different lexical blind spots. Both markdown AND graph recover a real chunk of divergence
        # offline, so the divergence label is hashing-specific (also guards against a broken/empty graph).
        self.assertGreater(self.s["agg"]["divergence"][MARKDOWN], 0.0,
                           "markdown BM25 recovers some divergence offline (different blind spots)")
        self.assertGreater(self.s["agg"]["divergence"][GRAPH], 0.0,
                           "graph recovers some divergence offline (seed+links); guards an empty graph")

    def test_offline_controls_are_found(self) -> None:
        # Apparatus check: the lexical controls ARE retrieved offline by EVERY backend + fusion,
        # so a divergence miss is a real semantic gap, not a broken harness (graph included).
        for col in (MARKDOWN, VECTORS, GRAPH, *(_FUSION)):
            self.assertGreaterEqual(self.s["agg"]["control"][col], 0.8,
                                    f"offline {col} should retrieve the lexical controls")

    def test_offline_fusion_does_not_beat_lexical_on_the_mix(self) -> None:
        # Anti-theater: offline, fusion does NOT beat the best single (lexical) backend overall —
        # the win is a real-embedder phenomenon, deferred to the captained run (no fake offline win).
        best_single = self.s["best_single_overall"]
        for f in _FUSION:
            self.assertLessEqual(self.s["agg"]["overall"][f], best_single + 1e-9,
                                 f"offline {f} must not beat the best single backend (would be theater)")


class FusionBakeoffLiveTests(unittest.TestCase):
    """Captained sanity — SKIPPED without a key (the real measurement lives in work/, recorded as D028)."""

    def test_live_fusion_recovers_divergence_when_key_present(self) -> None:
        # Double opt-in so a key present in CI/local does NOT trigger paid network calls: BOTH
        # MEMEVAL_LIVE=1 AND VOYAGE_API_KEY are required. The real measurement lives in work/ (D028).
        if not (os.environ.get("MEMEVAL_LIVE") == "1" and os.environ.get("VOYAGE_API_KEY")):
            self.skipTest("set MEMEVAL_LIVE=1 + VOYAGE_API_KEY for the captained sanity; default runs from work/ (D028)")
        from memeval.stores.embedders import VoyageEmbedder
        s = score_fusion_bakeoff(embed=VoyageEmbedder())
        # Offline the hashing vectors backend gets exactly 0 divergence recall; with a REAL embedder it
        # must recover some — the clean "the real embedder works" sanity. (Whether fusion then beats
        # markdown-alone, and RRF vs score, is the full captained comparison in work/fusion_bakeoff_live.py.)
        self.assertGreater(s["agg"]["divergence"][VECTORS], 0.0,
                           "real-embedder vectors should recover divergence gold the hashing floor missed")


def _report() -> None:
    s = score_fusion_bakeoff()
    print(f"FUSION BAKE-OFF (offline hashing floor) — {len(s['rows'])} cases over the {len(CORPUS)}-memory "
          f"D019 haystack (K={K}). Real-embedder run is captained (work/fusion_bakeoff_live.py, D028).\n")
    hdr = f"{'scope':<11} " + " ".join(f"{c:>13}" for c in s["cols"])
    print(hdr)
    print("-" * len(hdr))
    for scope in ("overall", "divergence", "control"):
        print(f"{scope:<11} " + " ".join(f"{s['agg'][scope][c]:>13.3f}" for c in s["cols"]))
    print(f"\nbest single backend overall = {s['best_single_overall']:.3f}")
    print("Offline reading: the hashing VECTORS backend gets 0 divergence (the D019 headroom), but "
          "markdown BM25 / graph recover ~half (different lexical blind spots) — so score-fusion TIES the "
          "best single (markdown) and RRF trails it: NO offline win. The real test (does adding a real "
          "semantic embedder let fusion beat markdown-alone, and is RRF or score better?) is captained (D028).")


if __name__ == "__main__":
    _report()
