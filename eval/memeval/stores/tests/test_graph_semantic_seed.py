"""Graph semantic-seeding eval — the `embed=` seam seeds the graph by MEANING, not just tokens.

Owner: Brent. Eval-first: written before the seam it gates.

The graph's *front door* is seeding: ``search`` enters the link graph only at nodes whose content shares
LITERAL tokens with the query (Jaccard). A query that is semantically related but lexically divergent
(synonym / paraphrase / cross-lingual) therefore fails to seed the relevant node — and since traversal
starts from seeds, that node's whole linked neighborhood is unreachable. The typed edges (#84) and deeper
traversal (#85/#86) only help AFTER the right node is seeded.

This adds an ``embed=`` seam (the SAME seam ``SqliteVectorStore`` / ``SemanticRouterClassifier`` use): when
an embedder is injected, ``search`` also seeds nodes whose content is cosine-similar to the query — a
**hybrid** (the union of lexical + semantic seeds, so a lexical hit is never lost). Default ``embed=None``
is lexical-only and byte-equivalent (offline stays zero-dependency).

The honest two-part split (the D019/D020 lesson — offline can't show the real win because the hashing
default can't represent meaning either):

* **Headroom (real lexical path):** a fixture whose gold *entry* node shares NO tokens with the query →
  the default Jaccard seeder misses it → its link-only neighbor (the gold) is unreachable. recall 0.
* **Mechanism (rigged offline embedder):** an injected deterministic embedder, rigged so the query and that
  entry node are cosine-close, SEEDS the entry → traversal reaches the gold. recall 0 → 1. This proves the
  cosine-seeding SEAM is wired (a mechanism claim, like ``test_semantic_classifier``), NOT real semantic
  accuracy — that is the captained ``voyage-3-large`` run, deferred (a gitignored ``work/`` script).

RED before ``GraphStore`` honored the injected ``embed`` (it swallowed the kwarg → no semantic seed → the
entry node was never seeded), GREEN after.

Run from ``eval/``:  python3 -m unittest memeval.stores.tests.test_graph_semantic_seed
"""

from __future__ import annotations

import unittest

from memeval.schema import MemoryItem
from memeval.stores.graph_store import GraphStore, _cosine

# Coined, lexically-disjoint corpus. The query shares NO tokens with the meaning-only entry/gold (so the
# lexical seeder can't reach them); it DOES share one token with the lexical control node.
_QUERY = "zeta eta theta"
_ENTRY_CONTENT = "Coined alpha beta gamma."     # meaning-only entry — rigged cosine-close to the query
_GOLD_CONTENT = "Coined delta epsilon."         # the gold — reachable ONLY by traversing entry -> gold
_LEX_CONTENT = "zeta marker words."             # lexical control — shares the token "zeta" with the query

_ENTRY, _GOLD, _LEX = "sem_entry", "sem_gold", "lex_node"


class _RiggedEmbedder:
    """Offline deterministic embedder for the seam MECHANISM. Maps the fixture's texts to hand-chosen unit
    vectors so the meaning-only query and its entry node are cosine-close (1.0) while LEXICALLY disjoint,
    and everything else is orthogonal to the query (cosine 0). Records ``(text, input_type)`` like
    ``MockEmbedder`` so the document/query asymmetry is assertable.

    This SIMULATES a real embedder finding the meaning match — it proves the cosine-seeding seam is wired,
    NOT real semantic accuracy (that is the captained voyage-3-large run)."""

    _VECS = {
        _QUERY: [1.0, 0.0, 0.0],
        _ENTRY_CONTENT: [1.0, 0.0, 0.0],   # cosine 1.0 with the query -> semantic seed
        _GOLD_CONTENT: [0.0, 1.0, 0.0],    # orthogonal -> reached only by traversal, never by seeding
    }

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, text: str, *, input_type=None) -> list:
        self.calls.append((text, input_type))
        return self._VECS.get(text, [0.0, 0.0, 1.0])  # unknown texts -> orthogonal to the query


def _item(iid: str, content: str, links: list) -> MemoryItem:
    return MemoryItem(item_id=iid, content=content,
                      metadata={"okf_title": iid, "okf_links": links})


def _corpus(*, link_entry_to_gold: bool = True) -> list:
    return [
        _item(_ENTRY, _ENTRY_CONTENT, [["relates to", _GOLD]] if link_entry_to_gold else []),
        _item(_GOLD, _GOLD_CONTENT, []),
        _item(_LEX, _LEX_CONTENT, []),
        _item("noise_a", "Coined kappa lambda.", []),
        _item("noise_b", "Coined mu nu.", []),
    ]


def _build(embed=None, *, link_entry_to_gold: bool = True) -> GraphStore:
    g = GraphStore(embed=embed)
    for it in _corpus(link_entry_to_gold=link_entry_to_gold):
        g.write(it)
    return g


def _ids(hits) -> list:
    return [h.item_id for h in hits]


class GraphSemanticSeedTests(unittest.TestCase):
    def test_lexical_seeder_misses_meaning_only_entry(self) -> None:
        # HEADROOM (default lexical path): the query shares no tokens with the entry, so it is never seeded
        # and its link-only gold is unreachable. GREEN before and after (this is the gap, not the fix).
        hits = _ids(_build(embed=None).search(_QUERY, k=5))
        self.assertNotIn(_ENTRY, hits, "lexical seeder must MISS the meaning-only entry node")
        self.assertNotIn(_GOLD, hits, "gold (reachable only via the entry) must be unreachable lexically")

    def test_semantic_seeding_recovers_gold(self) -> None:
        # THE polarity carrier (MECHANISM): the rigged embedder makes query~entry cosine-close -> the entry
        # is SEEDED semantically -> traversal (relates_to) reaches the gold. RED until GraphStore honors the
        # injected embed (before that it swallowed the kwarg -> no semantic seed -> entry never seeded).
        hits = _ids(_build(embed=_RiggedEmbedder()).search(_QUERY, k=5))
        self.assertIn(_ENTRY, hits, "semantic seeding must SEED the meaning-only entry node")
        self.assertIn(_GOLD, hits, "traversal from the semantically-seeded entry must RECOVER the gold")

    def test_hybrid_preserves_lexical_seed(self) -> None:
        # No-regression: semantic seeding is a UNION with lexical, never a replacement — the lexical control
        # node (shares the token "zeta") is retrieved both with and without an embedder. GREEN before/after.
        self.assertIn(_LEX, _ids(_build(embed=None).search(_QUERY, k=5)),
                      "lexical control must be retrieved without an embedder")
        self.assertIn(_LEX, _ids(_build(embed=_RiggedEmbedder()).search(_QUERY, k=5)),
                      "lexical control must STILL be retrieved with an embedder (hybrid, not replace)")

    def test_recovery_requires_the_link(self) -> None:
        # Anti-theater: the gold is reached by graph TRAVERSAL, not by seeding. Strip the entry->gold link
        # and even with semantic seeding the gold is unreachable (its content is cosine-orthogonal to the
        # query, so it is never seeded directly).
        hits = _ids(_build(embed=_RiggedEmbedder(), link_entry_to_gold=False).search(_QUERY, k=5))
        self.assertIn(_ENTRY, hits, "entry is still seeded semantically")
        self.assertNotIn(_GOLD, hits,
                         "no link -> gold unreachable even with semantic seeding (traversal, not seeding)")

    def test_document_query_input_type_asymmetry(self) -> None:
        # Mechanism detail (the Voyage seam): node content is embedded as "document" (at write), the query as
        # "query" (at search) — the store carries the asymmetry through the embed seam.
        emb = _RiggedEmbedder()
        _build(embed=emb).search(_QUERY, k=5)
        doc_calls = [t for (t, it) in emb.calls if it == "document"]
        query_calls = [(t, it) for (t, it) in emb.calls if it == "query"]
        self.assertIn(_ENTRY_CONTENT, doc_calls, "node content must be embedded as input_type='document'")
        self.assertEqual(query_calls, [(_QUERY, "query")], "the query must be embedded as input_type='query'")

    def test_cosine_fails_loud_on_dim_mismatch(self) -> None:
        # Fail-loud on embedder/dim drift (matches SqliteVectorStore._cosine). A silent zip-truncation
        # would score two different-dim vectors 1.0 and forge a semantic seed from invalid embeddings.
        with self.assertRaises(ValueError):
            _cosine([1.0, 0.0], [1.0])

    def test_cosine_zero_for_missing_or_empty_embedding(self) -> None:
        # A graph node may carry no embedding (None) -> no semantic signal, never a raise (the dim check
        # must not fire on an absent vector).
        self.assertEqual(_cosine([1.0, 0.0, 0.0], None), 0.0)
        self.assertEqual(_cosine([], [1.0]), 0.0)


if __name__ == "__main__":
    unittest.main()
