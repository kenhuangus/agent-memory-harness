---
id: ADR-storage-006
domain: storage
title: Typed + directional graph edges; okf.py captures the link anchor and emits okf_links as (anchor, target) tuples
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D029/D030/D031 (capstone-workspace); PR #81, PR #84
---

# ADR-storage-006: Typed + directional graph edges; `okf.py` captures the link anchor and emits `okf_links` as `(anchor, target)` tuples

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

The v1 graph backend ([D014]) was an **untyped, undirected** in-memory link-graph: memories are nodes, OKF links are edges, `search` = token-overlap seed → depth-2 BFS, scoring `seed_overlap × decay**distance`. [`architecture.md`](../../architecture.md) describes `Mem` only as *"vectors · graph · markdown"* with no relation typing, and §3's `MemoryItem.okf_links` carried no relation.

A graph-retrieval eval (Step 0, [D029], PR #75) — built with a **link-stripped differential** so it grades the *graph mechanism* not lexical Jaccard (the first cut was caught as lexical theater by the cross-vendor gate; the fix: every assertion must behave differently with links vs without) — pinned the headroom: the untyped/undirected store **cannot** answer *"what does X depend on"* differently from *"what depends on X"*, and a wrong-direction/wrong-relation distractor leaks into both.

Two coupled changes closed it:

1. **Typed + directional edges in the store (D030, PR #81).** A shared `relations.py` vocabulary + `query_intent`; `graph_store.py` types each edge from its `okf_links` relation, maintains a reverse `_in` index, and does intent-driven directional traversal. The eval's discrimination slices flip to victory (leak 1→0).
2. **The OKF parser contract (D031, PR #84).** D030 made the *store* consume typed links, but `okf.py`'s `_LINK_RE` discarded the markdown `[anchor]` — where the relation verb lives — so **real OKF links still arrived untyped** (`relates_to`). /sanity caught the overclaim. Fixing it required changing the okf link-parsing contract.

## Options considered

- **Untyped store + undirected traversal (status quo / v1).** Can't discriminate direction or relation; the eval proves the headroom. Rejected.
- **For the parser shape: mixed `list[str | tuple]`** — emit a bare target string for untyped links, a `(anchor, target)` tuple for typed ones. Avoids editing Ken's smoke `okf_links` round-trip assertion, BUT requires `okf.py` to `import classify_relation` (an okf→stores coupling), a heterogeneous contract, and parse-time typing that can drift from the store's vocabulary. Rejected.
- **Always-tuple `(anchor, target)` (chosen)** — every link, typed or not, emits an `(anchor, target)` pair; the store classifies (untyped → `relates_to`). `okf.py` stays a **pure parser with no `stores/` import**; the graph store stays the single owner of relation classification; one uniform/lossless shape; a single classification point.
- **For query direction: full NL parsing.** Out of scope — that's the learned-router north-star (D007). v1 uses `query_intent`, a recall-safe heuristic.

## Decision

**Edges are typed and directed.** [`stores/relations.py`](../../eval/memeval/stores/relations.py) is the shared closed-enum relation vocabulary (`depends_on`/`calls`/`uses`/`imports`/`conflicts_with`/`contradicts`/`renames`/`impacts`, with **`relates_to` as the generic default**) plus each relation's traversal `OUT`/`IN`/`BOTH` direction and a `query_intent` classifier. [`graph_store.py`](../../eval/memeval/stores/graph_store.py) types each edge from its `okf_links` entry, maintains a reverse `_in` index, and traverses in the intent-driven direction — so *"what does X depend on"* (OUT) and *"what depends on X"* (IN) return **different** sets.

**`okf.py` captures the anchor (always-tuple).** `_LINK_RE` now captures `(anchor, target)`; `doc_to_memory_item` emits `okf_links` as `(anchor, target)` **tuples** end-to-end, so a real `[depends on](x.md)` link becomes a `depends_on` edge. Untyped links emit `(anchor, target)` too and are classified `relates_to` by the store — **a generic edge is traversed by ANY query in both directions**, so an untyped corpus behaves exactly as the pre-typed store (full back-compat). `query_intent` is **recall-safe**: clear forward/reverse phrasings resolve directionally; **ambiguous → traverse BOTH ways** (never drops the incoming-edge gold, never returns a wrong relation).

## Rationale

This is what makes the graph backend *distinctive* — relationship retrieval the keyword/vector stores can't do, now with **direction and type** so structural queries stop returning each other's answers. The always-tuple shape keeps the parser dumb and the store the single owner of relation typing — strictly cleaner than a mixed shape that would couple `okf.py` to the store's vocabulary and let parse-time typing drift. Recall-safe `query_intent` (resolve-when-clear, both-when-ambiguous) is the honest v1: it never silently drops an answer, and the long tail of NL direction parsing is consciously deferred to the learned router. The cross-vendor gate hammered `query_intent` across 7 rounds, each a real phrasing bug, until it was provably recall-safe.

## Tradeoffs & risks

- **`query_intent` is a heuristic, not a parser.** Ambiguous phrasings degrade to both-way traversal (lower precision, never lost recall). Full NL direction is the D007 north-star. Accepted and bounded by the recall-safe contract.
- **`okf_links` shape changed (a parser contract change).** Consumers that read `okf_links` must handle `(anchor, target)` tuples. Verified the *only* live consumer is `graph_store._entry_rel_target` (handles tuples); cost was a 2-line update to Ken's `test_smoke.py` round-trip assertion (team-approved). A future consumer that assumes bare strings would break — the always-tuple uniformity is the mitigation (no heterogeneous shape to mis-handle).
- **Back-compat rests on `relates_to` being a both-way wildcard.** If that fallback regressed, an untyped corpus (the D008 cascade fixture, `test_graph_store`) would change behavior. Locked by those suites staying green and the relations module's explicit "generic edge traversed by any query both ways" contract.
- **The doc shows neither typing nor the tuple shape.** architecture.md describes the graph as untyped and `MemoryItem.okf_links` as untyped. Doc-reconciliation owed.

## Consequences for the build

- **Policy:** edges carry a relation type derived from the OKF anchor; traversal is directional per `query_intent`; ambiguity is recall-safe both-way. The graph store is the **single owner** of relation classification — parsers (and any future link source) must NOT type links themselves; they emit `(anchor, target)` and let the store classify.
- **Contract-adjacent (the `okf_links` shape):** `doc_to_memory_item` emits `okf_links: list[tuple[anchor, target]]`. Any consumer of `okf_links` must accept tuples. Current exhaustive consumer: `graph_store._entry_rel_target`. (Not a frozen-protocol change — `okf_links` lives in `metadata`, not the `MemoryStore` signature.)
- **Affected files:** [`stores/relations.py`](../../eval/memeval/stores/relations.py) (vocab + direction + `query_intent`), [`stores/graph_store.py`](../../eval/memeval/stores/graph_store.py) (typed edges, `_in` reverse index, intent-driven traversal), [`eval/memeval/okf.py`](../../eval/memeval/okf.py) (`_LINK_RE` anchor capture, always-tuple `okf_links`).
- **Cross-links:** the graph's durability seam is [`ADR-storage-002`](ADR-storage-002-persist-graph-backend.md) (edges are rebuilt from these typed `okf_links` on load); deeper traversal is [D032]/[D033]; semantic seeding is [D034]; the Neo4j upgrade is [`ADR-storage-007`](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md).
- **Doc-reconciliation owed:** architecture.md §1/§3 — describe typed/directional graph edges and the `okf_links` `(anchor, target)` shape.
