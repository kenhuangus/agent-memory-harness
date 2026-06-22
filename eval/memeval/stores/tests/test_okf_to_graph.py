"""OKF→GraphStore INTEGRATION eval — the end-to-end discrimination differential (graph "Step 1b").

Owner: Brent. Eval-first: written BEFORE the ``okf.py`` change it gates.

What this is (and how it differs from ``test_graph_retrieval_evals.py``)
-----------------------------------------------------------------------
``test_graph_retrieval_evals.py`` exercises the GraphStore in isolation by HAND-CONSTRUCTING typed
``okf_links`` entries (``[relation, target]``). That proves the store's typed/directional traversal — but
it bypasses ``okf.py`` entirely. This eval closes the remaining seam: it builds every item by **PARSING
REAL OKF MARKDOWN** through :func:`memeval.okf.doc_to_memory_item`, so the relation that types each edge
must survive the *parser*. It is the gate for the ``okf.py`` "Step 1b" change — capturing the markdown
link ANCHOR text (the relation verb) at parse time — and is RED until that change lands.

The gap it pins (why it is RED against current code)
----------------------------------------------------
``okf.py``'s ``_LINK_RE`` (``okf.py:55``) captures only the link TARGET; the ``[...]`` anchor (where
"depends on" / "conflicts with" lives) is matched but discarded. So ``doc_to_memory_item`` emits
``okf_links`` (``okf.py:240-242``) as a list of BARE target strings, and the GraphStore types every parsed
edge as the generic ``relates_to`` — which is traversed by ANY query in BOTH directions. Against that
untyped corpus a directional ``depends_on``/OUT query traverses EVERYTHING, so the ``conflicts_with``
distractor **LEAKS** into top-k. Once ``okf.py`` captures the anchor, the distractor is a typed
``conflicts_with`` edge that a ``depends_on``/OUT query does NOT traverse → **EXCLUDED**. That polarity
(leak now, excluded after) is this eval's core signal, carried by
``test_discrimination_excludes_wrong_relation_and_direction``.

The anti-theater spine (mirrors the sibling eval)
-------------------------------------------------
* **Coined-token corpus** (Zephyr / Quasar / Vortex / Hub / Beta / …) with terse, stopword-light content,
  so a query lexically seeds ONLY its entry node; the gold and the distractor are reachable ONLY through
  links — never lexically. Locked by ``test_gold_and_distractor_are_lexically_inert`` (targets share no
  query tokens) and ``test_query_seeds_only_entry_node`` (each query seeds EXACTLY its entry node).
* **LINK-STRIPPED DIFFERENTIAL**: every assertion compares the store built from the REAL docs against the
  SAME docs with their markdown links regex-stripped from the body (so the strip goes through ``okf.py``
  too), and REQUIRES the top-k to DIFFER. If stripping links changes nothing, the case is lexical theater
  and the suite fails. The gold must be retrievable WITH links and NOT without — graph-caused, not lexical.

Shape-agnostic on the fix
-------------------------
The production ``okf.py`` fix may emit ``okf_links`` as ``(anchor, target)`` tuples, ``{"rel","target"}``
dicts, or a mixed ``list[str|tuple]`` — all of which the GraphStore already accepts (verified: the suite
goes GREEN under each). This eval therefore asserts the relation/anchor is RECOVERABLE end-to-end (the
directional query discriminates), NOT any particular ``okf_links`` shape.

Run the guard:    cd eval && python3 -m unittest memeval.stores.tests.test_okf_to_graph
Direct (gates):   cd eval && python3 -m memeval.stores.tests.test_okf_to_graph
Diagnostic table: cd eval && python3 -m memeval.stores.tests.test_okf_to_graph --report
"""

from __future__ import annotations

import re
import sys
import unittest
from dataclasses import dataclass

from memeval.okf import doc_to_memory_item
from memeval.stores.graph_store import GraphStore

K = 5

# Slices. The headline is DISCRIMINATION (directional/typed exclusion). CONTROL proves the harness can
# detect a real graph hit (a link-only-reachable neighbor) so the discrimination signal isn't vacuous.
DISCRIMINATION_SLICES = ("typed_direction", "relation_disambiguation")
CONTROL_SLICES = ("untyped_fallback",)
_ALL_SLICES = DISCRIMINATION_SLICES + CONTROL_SLICES

# Strip a whole markdown link "[anchor](target)" from a body. Removing BOTH the anchor verb tokens AND
# the target is the differential's premise: no edge survives parsing, and the relation verb leaves the
# body too. (We strip the markdown and re-parse through okf.py, rather than poking okf_links, so the
# stripped corpus also exercises the real parser.)
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]+\)")


@dataclass(frozen=True)
class OKFDoc:
    """A node authored as REAL OKF markdown. ``body`` carries markdown links ``[verb](target.md)`` whose
    anchor is the relation; ``doc_to_memory_item`` must recover that relation for the edge to be typed."""

    item_id: str
    body: str

    def to_doc(self, *, strip_links: bool = False) -> str:
        body = _MD_LINK.sub("", self.body).strip() if strip_links else self.body
        # Minimal conformant OKF doc: required ``type`` + an explicit id + a title. Links live in the body.
        return (
            "---\n"
            "type: Concept\n"
            f"x_item_id: {self.item_id}\n"
            f"title: {self.item_id}\n"
            "---\n\n"
            f"{body}\n"
        )


@dataclass(frozen=True)
class OKFCase:
    name: str
    slice: str
    query: str
    gold_ids: tuple
    distractor_ids: tuple = ()
    entry_id: str = ""   # the node the query is EXPECTED to lexically seed (premise lock)
    note: str = ""


# Coined-token corpus. Entity tokens (Zephyr/Quasar/…) are unique; the RELATION verbs (depend/conflict/
# call) appear ONLY inside link anchors, so stripping links removes them from the body too. Gold and
# distractor node CONTENTS share no query tokens — they are reachable only through links.
CORPUS = (
    # typed_direction: Zephyr --depends_on--> Quasar  and  Zephyr --conflicts_with--> Vortex.
    # "what does Zephyr depend on" must return Quasar (depends_on OUT) and EXCLUDE Vortex (conflicts_with).
    OKFDoc("zephyr", "Zephyr orchestrates ingestion; it [depends on](quasar.md) "
                     "and [conflicts with](vortex.md)."),
    OKFDoc("quasar", "Quasar persists partitions."),
    OKFDoc("vortex", "Vortex schedules batches."),
    # relation_disambiguation: Hub --depends_on-->Alpha, --conflicts_with-->Beta, --calls-->Gamma.
    OKFDoc("hub", "Hub coordinates flows; it [depends on](alpha.md), "
                  "[conflicts with](beta.md), and [calls](gamma.md)."),
    OKFDoc("alpha", "Alpha keeps ledgers."),
    OKFDoc("beta", "Beta mirrors archives."),
    OKFDoc("gamma", "Gamma scores anomalies."),
    # untyped_fallback control: Solis --(generic)--> Luna, reachable ONLY via the link.
    OKFDoc("solis", "Solis indexes documents; see also [related](luna.md)."),
    OKFDoc("luna", "Luna ranks candidates."),
    # inert haystack (coined, link-free, disjoint tokens) so k is selective.
    OKFDoc("nimbus", "Nimbus caches fragments."),
    OKFDoc("stratus", "Stratus compresses blobs."),
    OKFDoc("cobalt", "Cobalt aggregates metrics."),
    OKFDoc("onyx", "Onyx throttles producers."),
    OKFDoc("jade", "Jade encrypts volumes."),
    OKFDoc("saffron", "Saffron buffers streams."),
    OKFDoc("indigo", "Indigo dedupes records."),
)

CASES = (
    # THE headline: a depends_on OUT query returns the depends_on gold and excludes the conflicts_with
    # distractor (reachable only via links). RED today (untyped relates_to -> distractor leaks both ways).
    OKFCase("zephyr-depends-out", "typed_direction", "what does Zephyr depend on",
            gold_ids=("quasar",), distractor_ids=("vortex",), entry_id="zephyr",
            note="gold = Zephyr's depends_on OUT target (Quasar); Vortex is a conflicts_with neighbor the "
                 "UNTYPED store cannot exclude (it leaks via the relates_to-both-ways fallback). Typed "
                 "parsing makes the conflicts_with edge un-traversed by a depends_on/OUT query."),
    OKFCase("hub-depends-relation", "relation_disambiguation", "what does Hub depend on",
            gold_ids=("alpha",), distractor_ids=("beta", "gamma"), entry_id="hub",
            note="gold = the depends_on target (Alpha); Beta (conflicts_with) and Gamma (calls) are "
                 "other-relation neighbors the untyped store cannot exclude."),
    OKFCase("hub-conflict-relation", "relation_disambiguation", "what does Hub conflict with",
            gold_ids=("beta",), distractor_ids=("alpha", "gamma"), entry_id="hub",
            note="gold = the conflicts_with target (Beta); Alpha (depends_on) and Gamma (calls) are "
                 "other-relation neighbors the untyped store cannot exclude."),
    OKFCase("solis-related-control", "untyped_fallback", "Solis related",
            gold_ids=("luna",), entry_id="solis",
            note="control: Luna is Solis's direct link target, lexically inert -> reachable ONLY via the "
                 "link. Proves the harness detects a real graph hit (so discrimination isn't vacuous)."),
)

_CORPUS_IDS = {d.item_id for d in CORPUS}
_EXPECTED_CORPUS = 16   # size lock — changing the corpus is deliberate
_EXPECTED_CASES = 4     # count lock — changing the case set is deliberate


def _stripped_tokens(body: str) -> set:
    """Tokens of a node's body AS THE SEEDER SEES IT — markdown links removed (the relation verbs and the
    target slugs live in anchors that only contribute edges, never seed content). Uses the store's own
    tokenizer so this premise check matches the seeder exactly."""
    from memeval.stores.graph_store import _tokenize
    return set(_tokenize(_MD_LINK.sub("", body)))


def _build_graph(*, strip_links: bool = False) -> GraphStore:
    """Build the store by PARSING each doc's OKF markdown through ``okf.py`` (never hand-set okf_links)."""
    g = GraphStore()
    for d in CORPUS:
        item = doc_to_memory_item(d.to_doc(strip_links=strip_links), fallback_id=d.item_id)
        g.write(item)
    return g


def _top_ids(g: GraphStore, query: str, *, k: int = K) -> list:
    return [h.item.item_id for h in g.search(query, k=k)]


def evaluate(case: OKFCase, g_links: GraphStore, g_nolinks: GraphStore, *, k: int = K) -> dict:
    bad = tuple(x for x in (case.gold_ids + case.distractor_ids) if x not in _CORPUS_IDS)
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
        "top_with": wl, "top_without": nl,
    }


class OKFParseContractTests(unittest.TestCase):
    """The integration premise: ``okf.py`` must turn each authored markdown link into an edge the
    GraphStore can traverse, and the link ANCHOR (relation verb) must be RECOVERABLE — shape-agnostically.
    These assert RECOVERABILITY (the directional query discriminates), not any okf_links shape, and lock
    the differential's lexical premises so a later failure is about typing, not plumbing."""

    def test_corpus_and_cases_well_formed(self) -> None:
        # Size/count locks: GREEN today and after the fix. They make a corpus or case-set edit DELIBERATE
        # (you must update the lock), so the differential's premises below can't silently rot.
        self.assertEqual(len(CORPUS), _EXPECTED_CORPUS, "corpus size changed — update _EXPECTED_CORPUS")
        self.assertEqual(len(CASES), _EXPECTED_CASES, "case count changed — update _EXPECTED_CASES")
        ids = [d.item_id for d in CORPUS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate corpus id")
        names = [c.name for c in CASES]
        self.assertEqual(len(names), len(set(names)), "duplicate case name")
        for c in CASES:
            self.assertIn(c.slice, _ALL_SLICES, f"{c.name}: bad slice {c.slice!r}")
            self.assertTrue(c.query.strip() and c.note.strip(), f"{c.name}: empty query/note")
            self.assertTrue(c.gold_ids, f"{c.name}: needs >=1 gold id")
            self.assertIn(c.entry_id, _CORPUS_IDS, f"{c.name}: entry_id {c.entry_id!r} not in corpus")

    def test_docs_parse_and_id_resolves(self) -> None:
        # GREEN today and after: the store must build from PARSED docs and every doc's x_item_id must
        # resolve to a node (so a later discrimination failure is about edge TYPING, not parsing/plumbing).
        g = _build_graph()
        for d in CORPUS:
            self.assertIsNotNone(g.get(d.item_id), f"{d.item_id}: doc did not parse into a node")

    def test_links_are_parsed_from_markdown_not_handset(self) -> None:
        # GREEN today and after — NOT a polarity carrier. Sanity: the linking docs DO yield okf_links via
        # okf.py (so the corpus actually exercises the parser, and the differential below isn't vacuous on
        # an empty okf_links). We do NOT assert the entry SHAPE — only that some links were captured; the
        # discrimination test carries the RED→GREEN polarity. Today this passes with BARE-target strings;
        # after the fix it passes with typed entries — by design it cannot distinguish the two.
        item = doc_to_memory_item(CORPUS[0].to_doc(), fallback_id="zephyr")
        self.assertTrue(item.metadata.get("okf_links"),
                        "Zephyr's body links were not captured by okf.py at all — parser regression")

    def test_gold_and_distractor_are_lexically_inert(self) -> None:
        # GREEN today and after — a premise LOCK, not a polarity carrier. Anti-drift: gold / distractor
        # node CONTENTS (link-stripped, as the seeder sees them) must share NO query tokens, so they are
        # reachable ONLY through links. If a future edit made one lexically findable, the leak/recall
        # assertions could pass for the wrong (lexical) reason — this fails first to catch that.
        by_id = {d.item_id: d for d in CORPUS}
        for c in CASES:
            from memeval.stores.graph_store import _tokenize
            q = set(_tokenize(c.query))
            for iid in c.gold_ids + c.distractor_ids:
                overlap = q & _stripped_tokens(by_id[iid].body)
                self.assertFalse(overlap, f"{c.name}: {iid} shares query tokens {overlap} — it would be "
                                          f"lexically seedable, not link-reachable-only (theater risk)")

    def test_query_seeds_only_entry_node(self) -> None:
        # GREEN today and after — the differential's OTHER premise lock, not a polarity carrier. Each query
        # must lexically seed EXACTLY its entry node (and no other). If a corpus edit let a query seed the
        # gold/distractor (or a second node) directly, the graph claim would be undermined — the result
        # could be a lexical hit, not a traversal. Lock it so the RED→GREEN signal stays purely graph-typed.
        from memeval.stores.graph_store import _tokenize
        for c in CASES:
            q = set(_tokenize(c.query))
            seeds = [d.item_id for d in CORPUS if q & _stripped_tokens(d.body)]
            self.assertEqual(seeds, [c.entry_id],
                             f"{c.name}: query lexically seeds {seeds}, expected only its entry "
                             f"{[c.entry_id]} — anything else is lexical leakage (theater risk)")

    def test_evaluate_fails_fast_on_unknown_id(self) -> None:
        # GREEN today and after — harness self-check: a typo'd gold/distractor id raises rather than
        # masquerading as a (missing) result, so a real assertion can never silently pass on a bad id.
        g = _build_graph()
        with self.assertRaises(ValueError):
            evaluate(OKFCase("_x", "typed_direction", "q", ("nope",)), g, g)


class OKFToGraphDifferentialTests(unittest.TestCase):
    """Anti-theater core: every case must behave DIFFERENTLY with links vs link-stripped, against the
    store built FROM PARSED OKF MARKDOWN. The discrimination cases additionally require the wrong-relation
    distractor to be EXCLUDED — which only happens once okf.py types the edge from the anchor."""

    @classmethod
    def setUpClass(cls) -> None:
        gl, gn = _build_graph(), _build_graph(strip_links=True)
        cls.results = {c.name: evaluate(c, gl, gn) for c in CASES}

    def test_links_change_every_case(self) -> None:
        # GREEN today and after — the anti-theater differential. Removing all markdown links (re-parsed
        # through okf.py) must change the top-k of EVERY case; otherwise that case is lexical, not graph.
        # (Holds in BOTH polarities: even untyped relates_to edges change top-k vs no edges — this guards
        # that the corpus is link-driven at all, independent of the typing fix.)
        for c in CASES:
            r = self.results[c.name]
            self.assertNotEqual(r["top_with"], r["top_without"],
                                f"{c.name}: link-stripping did not change top-k — lexical, not graph")

    def test_control_gold_reached_only_via_links(self) -> None:
        # GREEN today and after — the CONTROL that proves the harness can DETECT a real graph hit, so the
        # discrimination signal below isn't vacuous. Luna is reachable from Solis only via its (generic
        # relates_to) link: present WITH links, absent WITHOUT. Uses a generic "related" anchor, so it
        # behaves identically before and after the typing fix — deliberately not a polarity carrier.
        for c in CASES:
            if c.slice in CONTROL_SLICES:
                r = self.results[c.name]
                self.assertEqual(r["recall_with"], 1.0,
                                 f"{c.name}: control gold must be retrieved with links")
                self.assertLess(r["recall_without"], 1.0,
                                f"{c.name}: control gold must be UNreachable without links (else lexical)")

    def test_discrimination_excludes_wrong_relation_and_direction(self) -> None:
        """THE core signal — RED on current code, GREEN once okf.py captures the link anchor.

        Per assertion, WHY it is RED today:

        * ``recall_with == 1.0`` — GREEN today already: the depends_on/conflicts_with gold is a direct
          link target, and even an UNTYPED relates_to edge is traversed by every query, so the gold is
          retrieved with links in BOTH polarities. This assertion guards that the headline is real
          discrimination over a PRESENT gold, never a vacuous empty result.

        * ``not leak_with`` — THE RED ASSERTION. On current code okf.py discards the anchor
          (``_LINK_RE`` captures only the target), so the distractor's "conflicts with" / "calls" link
          parses to an UNTYPED relates_to edge. A depends_on/OUT (or conflicts_with) query traverses
          relates_to edges BOTH ways (graph_store ``_neighbors_for``), so the wrong-relation distractor
          LEAKS into top-k and ``leak_with`` is non-empty → this FAILS today. Once okf.py captures the
          anchor, the link parses to a TYPED conflicts_with/calls edge that a depends_on/OUT query does
          NOT traverse → the distractor is EXCLUDED, ``leak_with`` is empty → GREEN. (Empirically: today
          zephyr-depends-out leaks ['vortex']; hub-depends-relation leaks ['beta','gamma']; after the
          anchor-capture fix all leaks collapse to [].)

        * ``recall_without < 1.0`` — GREEN today already: with links stripped (no edge at all) the gold
          is UNREACHABLE, proving the gold arrives by graph traversal of the parsed edge, not by lexical
          overlap. Keeps the leak exclusion attributable to edge TYPING, never to top-k truncation.
        """
        for c in CASES:
            if c.slice in DISCRIMINATION_SLICES:
                r = self.results[c.name]
                self.assertEqual(r["recall_with"], 1.0,
                                 f"{c.name}: typed store must retrieve the gold with links "
                                 f"(top_with={r['top_with']})")
                self.assertFalse(r["leak_with"],
                                 f"{c.name}: the relation/anchor was NOT recovered from the markdown — the "
                                 f"wrong-relation/direction distractor leaked: {r['leak_with']} "
                                 f"(top_with={r['top_with']}). okf.py must capture the link anchor "
                                 f"(currently okf.py _LINK_RE captures only the target, so the edge is "
                                 f"untyped relates_to and a depends_on/OUT query traverses it both ways).")
                # Still graph-caused, not lexical: the gold is reachable only via links.
                self.assertLess(r["recall_without"], 1.0,
                                f"{c.name}: gold reachable without links — case is lexical, not graph")


def _report() -> None:
    gl, gn = _build_graph(), _build_graph(strip_links=True)
    print(f"OKF→GRAPH INTEGRATION EVAL — {len(CASES)} cases over a {len(CORPUS)}-node corpus (K={K}). "
          f"Items built by PARSING OKF markdown via okf.doc_to_memory_item.\n")
    print(f"{'slice':<24} {'case':<22} {'rec_w':>6} {'rec_n':>6} {'leak_w':>7}")
    print("-" * 68)
    for c in CASES:
        r = evaluate(c, gl, gn)
        print(f"{c.slice:<24} {c.name:<22.22} {r['recall_with']:>6.2f} {r['recall_without']:>6.2f} "
              f"{len(r['leak_with']):>7}")
    print("\nEvery row CHANGES when links are stripped (the anti-theater differential), proving results are "
          "GRAPH-caused, not lexical. The discrimination rows require leak_w=0 — the gold returned and the "
          "wrong-relation distractor EXCLUDED — which holds only once okf.py captures the markdown link "
          "anchor (the relation verb) at parse time. RED until then.")


if __name__ == "__main__":
    # Default run GATES via unittest (exits non-zero when RED). `--report` opts into the diagnostic table
    # FIRST (no assertions), then still falls through to the asserting suite so direct invocation always
    # gates.
    if "--report" in sys.argv:
        sys.argv.remove("--report")
        _report()
        print()
    unittest.main()
