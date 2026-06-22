"""Graph-retrieval eval — link-dependent instrument across the graph arc (Steps 0→1). Owner: Brent.

Eval-first: began as the Step-0 headroom instrument (eval only — it pinned, against the then-untyped
store, exactly where relational retrieval failed, so the typed-edge work had a target). It now also gates
**Step 1**: the same cases assert the typed/directional store's VICTORY on the discrimination slices,
while the reach/seed slices stay headroom. (Step 1's ``graph_store.py`` change ships in the same branch as
this eval; the link-stripped differential below keeps every case graph-caused, not lexical.)

As of **Step 1**, the ``GraphStore`` is **typed + directional**: each OKF link is typed from its anchor
(via :mod:`memeval.stores.relations`) and ``search`` resolves the query's ``(relation, direction)`` intent,
traversing only matching edges (still seeds by Jaccard token overlap; still depth-2 BFS). So the
discrimination slices below now **pass** (the headroom-to-victory flip); the reach/seed slices remain
headroom for later primitives.

**Why a coined-token corpus + a link-stripped DIFFERENTIAL (the anti-theater core).** A first cut of this
eval used natural-language nodes and was rejected by cross-vendor review: rebuilding it with every
``okf_links`` removed produced identical results, proving it measured lexical Jaccard ranking, not the
graph. So this version uses a controlled corpus of COINED entity tokens (Zephyr/Quasar/Hub/…) with terse,
stopword-light content, so a query lexically seeds ONLY its entry node and the gold/neighbors are
reachable ONLY through links. Every assertion is then a DIFFERENTIAL: it compares the real store WITH
links vs the same store with links stripped, and requires them to DIFFER. If stripping links doesn't
change a case, that case is lexical theater and fails the suite.

Four slices (the typed-edge headroom Step 1 must recover; all link-differential-provable):

* **typed_direction** / **relation_disambiguation** — DISCRIMINATION (Step 1: now PASSING): the typed store
  traverses only the query's matching ``(relation, direction)`` edges, so it retrieves the gold and
  EXCLUDES the wrong-direction/wrong-relation distractor (asserted: ``recall_with==1`` and ``not
  leak_with``). Pre-Step-1 the distractor leaked — the headroom this slice pinned.
* **multi_hop** — REACH: a depth<=2 probe IS reached *with* links (traversal works) while the depth-3 gold
  is MISSED (beyond ``_MAX_DEPTH``) — asserted: gold recall==0, probe reached with-links-only.
* **untyped_fallback** — CONTROL: a direct 1-hop neighbor reachable ONLY via its link; the store retrieves
  it *with* links and not *without* (asserted: recall 1 with links, <1 without). Proves the harness can
  detect graph-retrieval success — a real graph control, not a lexical hit.

(``semantic_seed`` — an entry node findable only by meaning, not tokens — is deliberately NOT here: it is
an EMBEDDER-seeding headroom, not a typed-edge one, and can't be shown by the link differential. It
belongs with the captained embedder work.)

**Status / forward use.** Step 1 (typed/directional edges) landed → the discrimination slices now PASS.
Still headroom: **multi_hop** needs **deeper / path-aware traversal** (raising ``_MAX_DEPTH`` or a path
query) — a separate primitive from typed edges; and **semantic_seed** (dropped here) needs embedder
seeding — a third, independent primitive. Those flip to victory only when their respective work lands.

Reproduce the report: cd eval && python3 -m memeval.stores.tests.test_graph_retrieval_evals
Run the guard:        cd eval && python3 -m unittest memeval.stores.tests.test_graph_retrieval_evals
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore

K = 5

DISCRIMINATION_SLICES = ("typed_direction", "relation_disambiguation")
REACH_SLICES = ("multi_hop",)
CONTROL_SLICES = ("untyped_fallback",)
_ALL_SLICES = DISCRIMINATION_SLICES + REACH_SLICES + CONTROL_SLICES


@dataclass(frozen=True)
class GraphItem:
    """A memory node. ``links`` are DIRECTED typed edges (relation, target_id); the store types each edge
    from its anchor and resolves the query's (relation, direction) intent — what the discrimination slices
    verify. (``_mk`` writes these as typed ``[relation, target]`` entries in ``okf_links``.)"""

    item_id: str
    content: str
    links: tuple = ()  # tuple of (relation, target_id)


@dataclass(frozen=True)
class GraphCase:
    name: str
    slice: str
    query: str
    gold_ids: tuple
    distractor_ids: tuple = ()
    probe_ids: tuple = ()   # depth<=2 nodes traversal SHOULD reach (multi_hop)
    note: str = ""


# Coined-token corpus: query verbs (depend/conflict/call/related/chain) are absent from node content, so
# only the entity token seeds and gold/neighbors are reachable ONLY via links (see module docstring).
CORPUS = (
    # typed_direction: Vortex --depends_on--> Zephyr --depends_on--> Quasar
    GraphItem("td-zephyr", "Zephyr orchestrates ingestion.", (("depends on", "td-quasar"),)),
    GraphItem("td-quasar", "Quasar persists partitions."),
    GraphItem("td-vortex", "Vortex schedules batches.", (("depends on", "td-zephyr"),)),
    # relation_disambiguation: Hub --depends_on-->Alpha, --conflicts_with-->Beta, --calls-->Gamma
    GraphItem("rd-hub", "Hub coordinates flows.",
              (("depends on", "rd-alpha"), ("conflicts with", "rd-beta"), ("calls", "rd-gamma"))),
    GraphItem("rd-alpha", "Alpha keeps ledgers."),
    GraphItem("rd-beta", "Beta mirrors archives."),
    GraphItem("rd-gamma", "Gamma scores anomalies."),
    # multi_hop chain: Apex->Bravo->Charlie->Delta (Delta at depth 3, beyond _MAX_DEPTH=2)
    GraphItem("mh-apex", "Apex receives sessions.", (("calls", "mh-bravo"),)),
    GraphItem("mh-bravo", "Bravo validates payloads.", (("calls", "mh-charlie"),)),
    GraphItem("mh-charlie", "Charlie reserves stock.", (("calls", "mh-delta"),)),
    GraphItem("mh-delta", "Delta commits writes."),
    # untyped_fallback controls: Solis --relates_to--> Luna ; Nimbus --relates_to--> Stratus
    GraphItem("uf-solis", "Solis indexes documents.", (("relates to", "uf-luna"),)),
    GraphItem("uf-luna", "Luna ranks candidates."),
    GraphItem("uf-nimbus", "Nimbus caches fragments.", (("relates to", "uf-stratus"),)),
    GraphItem("uf-stratus", "Stratus compresses blobs."),
    # inert haystack (coined, link-free, disjoint tokens) so k is selective
    GraphItem("noise-mistral", "Mistral rotates credentials."),
    GraphItem("noise-cobalt", "Cobalt aggregates metrics."),
    GraphItem("noise-onyx", "Onyx throttles producers."),
    GraphItem("noise-jade", "Jade encrypts volumes."),
    GraphItem("noise-saffron", "Saffron buffers streams."),
    GraphItem("noise-indigo", "Indigo dedupes records."),
)

CASES = (
    GraphCase("dependency-out-edge", "typed_direction", "Zephyr dependency",
              gold_ids=("td-quasar",), distractor_ids=("td-vortex",),
              note="gold = Zephyr's depends_on OUT target (Quasar); Vortex is the IN dependent (wrong "
                   "direction) the undirected store also returns."),
    GraphCase("dependents-in-edge", "typed_direction", "Zephyr dependents impact",
              gold_ids=("td-vortex",), distractor_ids=("td-quasar",),
              note="gold = what depends on Zephyr (Vortex, IN); Quasar is Zephyr's OUT dependency (wrong "
                   "direction)."),
    GraphCase("conflict-relation", "relation_disambiguation", "Hub conflict",
              gold_ids=("rd-beta",), distractor_ids=("rd-alpha", "rd-gamma"),
              note="gold = the conflicts_with target (Beta); Alpha (depends_on) + Gamma (calls) are "
                   "other-relation neighbors the untyped store cannot exclude."),
    GraphCase("depends-relation", "relation_disambiguation", "Hub dependency",
              gold_ids=("rd-alpha",), distractor_ids=("rd-beta", "rd-gamma"),
              note="gold = the depends_on target (Alpha); Beta/Gamma are other-relation neighbors."),
    GraphCase("calls-relation", "relation_disambiguation", "Hub callee",
              gold_ids=("rd-gamma",), distractor_ids=("rd-alpha", "rd-beta"),
              note="gold = the calls target (Gamma); Alpha/Beta are other-relation neighbors."),
    GraphCase("deep-chain-tail", "multi_hop", "Apex chain tail",
              gold_ids=("mh-delta",), probe_ids=("mh-bravo", "mh-charlie"),
              note="gold = Delta at depth 3 (beyond _MAX_DEPTH=2 -> missed); Bravo(1)/Charlie(2) are the "
                   "in-reach probe traversal DOES reach with links."),
    GraphCase("neighbor-solis", "untyped_fallback", "Solis related",
              gold_ids=("uf-luna",),
              note="control: Luna is Solis's direct link target, lexically inert -> reachable ONLY via "
                   "the link."),
    GraphCase("neighbor-nimbus", "untyped_fallback", "Nimbus related",
              gold_ids=("uf-stratus",),
              note="control: Stratus is Nimbus's direct link target, lexically inert -> reachable ONLY "
                   "via the link."),
)

_CORPUS_IDS = {it.item_id for it in CORPUS}
_EXPECTED_CORPUS = 21   # size lock — changing the corpus is deliberate
_EXPECTED_CASES = 8     # count lock — changing the case set is deliberate


def _mk(it: "GraphItem", *, strip_links: bool = False) -> MemoryItem:
    # Typed links: each okf_links entry is [relation, target] so the store types the edge from the anchor
    # (Step 1). strip_links empties okf_links -> no edges (the differential's premise).
    links = [] if strip_links else [[rel, t] for rel, t in it.links]
    return MemoryItem(item_id=it.item_id, content=it.content,
                      metadata={"okf_title": it.item_id, "okf_links": links})


def _build_graph(*, strip_links: bool = False) -> GraphStore:
    g = GraphStore()
    for it in CORPUS:
        g.write(_mk(it, strip_links=strip_links))
    return g


def _top_ids(g: GraphStore, query: str, *, k: int = K) -> list:
    return [h.item_id for h in g.search(query, k=k)]


def evaluate(case: GraphCase, g_links: GraphStore, g_nolinks: GraphStore, *, k: int = K) -> dict:
    # Fail fast on a typo'd id rather than let it masquerade as a result.
    bad = tuple(x for x in (case.gold_ids + case.distractor_ids + case.probe_ids) if x not in _CORPUS_IDS)
    if bad:
        raise ValueError(f"{case.name}: ids not in corpus: {bad}")
    wl, nl = _top_ids(g_links, case.query, k=k), _top_ids(g_nolinks, case.query, k=k)

    def _recall(top):
        return sum(1 for x in case.gold_ids if x in top) / len(case.gold_ids)

    return {
        "name": case.name, "slice": case.slice,
        "recall_with": _recall(wl), "recall_without": _recall(nl),
        "leak_with": [d for d in case.distractor_ids if d in wl],
        "leak_without": [d for d in case.distractor_ids if d in nl],
        "probe_with": [p for p in case.probe_ids if p in wl],
        "probe_without": [p for p in case.probe_ids if p in nl],
        "top_with": wl, "top_without": nl,
    }


class GraphFixtureContractTests(unittest.TestCase):
    def test_corpus_and_cases_well_formed(self) -> None:
        self.assertEqual(len(CORPUS), _EXPECTED_CORPUS, "corpus size changed — update _EXPECTED_CORPUS")
        self.assertEqual(len(CASES), _EXPECTED_CASES, "case count changed — update _EXPECTED_CASES")
        ids = [it.item_id for it in CORPUS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate corpus id")
        names = [c.name for c in CASES]
        self.assertEqual(len(names), len(set(names)), "duplicate case name")
        for c in CASES:
            self.assertIn(c.slice, _ALL_SLICES, f"{c.name}: bad slice {c.slice!r}")
            self.assertTrue(c.query.strip() and c.note.strip(), f"{c.name}: empty query/note")
            self.assertTrue(c.gold_ids, f"{c.name}: needs >=1 gold id")
        for it in CORPUS:
            for _, tgt in it.links:
                self.assertIn(tgt, _CORPUS_IDS, f"{it.item_id}: dangling link target {tgt!r}")

    def test_every_slice_has_cases(self) -> None:
        present = {c.slice for c in CASES}
        for s in _ALL_SLICES:
            self.assertIn(s, present, f"slice {s!r} has no cases")

    def test_gold_distractor_probe_are_lexically_inert(self) -> None:
        # Anti-drift lock (the differential's premise): gold / distractor / probe nodes must share NO
        # query tokens, so they are reachable ONLY through links. If a future edit makes one lexically
        # findable, the leak/recall assertions could pass for the wrong (lexical) reason — fail here first.
        from memeval.stores.graph_store import _tokenize
        by_id = {it.item_id: it for it in CORPUS}
        for c in CASES:
            q = set(_tokenize(c.query))
            for iid in c.gold_ids + c.distractor_ids + c.probe_ids:
                overlap = q & set(_tokenize(by_id[iid].content))
                self.assertFalse(overlap, f"{c.name}: {iid} shares query tokens {overlap} — it would be "
                                          f"lexically seedable, not link-reachable-only (theater risk)")

    def test_evaluate_fails_fast_on_unknown_id(self) -> None:
        g = _build_graph()
        with self.assertRaises(ValueError):
            evaluate(GraphCase("_x", "multi_hop", "q", ("nope",)), g, g)


class GraphLinkDifferentialTests(unittest.TestCase):
    """Anti-theater core: every case must behave DIFFERENTLY with links vs link-stripped, against the
    REAL current store. This is what a first cut lacked (stripping links changed nothing)."""

    @classmethod
    def setUpClass(cls) -> None:
        gl, gn = _build_graph(), _build_graph(strip_links=True)
        cls.results = {c.name: evaluate(c, gl, gn) for c in CASES}

    def test_links_change_every_case(self) -> None:
        # THE guard the first cut failed: removing all okf_links must change the top-k of EVERY case —
        # otherwise the case is lexical, not graph.
        for c in CASES:
            r = self.results[c.name]
            self.assertNotEqual(r["top_with"], r["top_without"],
                                f"{c.name}: link-stripping did not change top-k — lexical, not graph")

    def test_control_gold_reached_only_via_links(self) -> None:
        for c in CASES:
            if c.slice in CONTROL_SLICES:
                r = self.results[c.name]
                self.assertEqual(r["recall_with"], 1.0, f"{c.name}: control gold must be retrieved with links")
                self.assertLess(r["recall_without"], 1.0,
                                f"{c.name}: control gold must be UNreachable without links (else lexical)")

    def test_discrimination_excludes_wrong_direction_and_relation(self) -> None:
        # Step 1 (typed/directional edges): the store resolves the query's (relation, direction) intent and
        # traverses only matching edges, so it RETRIEVES the gold and EXCLUDES the wrong-direction /
        # wrong-relation distractor. Pre-Step-1 these distractors LEAKED — the headroom this slice pinned;
        # this is the headroom-to-victory flip.
        for c in CASES:
            if c.slice in DISCRIMINATION_SLICES:
                r = self.results[c.name]
                self.assertEqual(r["recall_with"], 1.0,
                                 f"{c.name}: typed store must retrieve the gold with links")
                self.assertFalse(r["leak_with"],
                                 f"{c.name}: typed store must EXCLUDE the wrong-direction/relation "
                                 f"distractor, but leaked: {r['leak_with']}")
                # Still graph-caused, not lexical: the gold is reachable only via links.
                self.assertLess(r["recall_without"], 1.0,
                                f"{c.name}: gold reachable without links — case is lexical, not graph")

    def test_multihop_reaches_probe_not_deep_gold(self) -> None:
        for c in CASES:
            if c.slice in REACH_SLICES:
                r = self.results[c.name]
                self.assertEqual(r["recall_with"], 0.0,
                                 f"{c.name}: depth-3 gold must be MISSED by the depth-2 store (reach headroom)")
                self.assertTrue(r["probe_with"],
                                f"{c.name}: a depth<=2 probe must be reached with links (traversal works)")
                self.assertFalse(r["probe_without"],
                                 f"{c.name}: probe reached without links — lexical, not traversal")


def _report() -> None:
    gl, gn = _build_graph(), _build_graph(strip_links=True)
    print(f"GRAPH-RETRIEVAL EVAL — {len(CASES)} cases over a {len(CORPUS)}-node corpus (K={K}). "
          f"Store = typed/directional (Step 1); depth-2 BFS; Jaccard seed.\n")
    print(f"{'slice':<24} {'case':<22} {'rec_w':>6} {'rec_n':>6} {'leak_w':>7} {'probe_w':>8}")
    print("-" * 76)
    for c in CASES:
        r = evaluate(c, gl, gn)
        print(f"{c.slice:<24} {c.name:<22.22} {r['recall_with']:>6.2f} {r['recall_without']:>6.2f} "
              f"{len(r['leak_with']):>7} {len(r['probe_with']):>8}")
    print("\nEvery row CHANGES when links are stripped (the anti-theater differential), proving results are "
          "GRAPH-caused, not lexical. Step 1 typed/directional edges: discrimination now retrieves the gold "
          "with leak_w=0 (was leaking). multi_hop stays headroom (depth-2 limit) until deeper traversal lands.")


if __name__ == "__main__":
    _report()
