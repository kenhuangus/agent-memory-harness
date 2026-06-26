---
id: ADR-storage-010
domain: storage
title: FalkorDB native typed-graph backend — opt-in native traversal beats the in-memory baseline on a structural case (captained); the parity floor stays the default
status: Accepted
date: 2026-06-26
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D044/D056/D058/D059 (capstone-workspace); GRAPH_STORE_SCOPE.md
---

# ADR-storage-010: FalkorDB native typed-graph backend — opt-in native traversal beats the in-memory baseline on a structural case; the parity floor stays the default

**Status:** Accepted · **Date:** 2026-06-26 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

The in-memory [`GraphStore`](../../eval/memeval/stores/graph_store.py) — and the Neo4j Phase-A floor it backs ([`ADR-storage-007`](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md)) — score relational retrieval by seeding, then traversing typed/directional edges ([`ADR-storage-006`](ADR-storage-006-typed-directional-graph-edges-okf-anchor-tuple.md)) with a flat `0.5^hops` decay over the **single best path** to each node. That model structurally **cannot express path multiplicity** (a node reachable from the seed by *many* convergent paths is no better-ranked than one reachable by a single path at the same hop-distance) nor importance-weighted distance. A node-graph database can.

A new backend, `FalkorGraphStore` (FalkorDB — a Redis-protocol graph DB speaking openCypher via `GRAPH.QUERY`), shipped in two layers:
- **PR1 — parity floor (#193):** parity-by-construction — `write` persists `:Memory` nodes + the `okf_links` SSOT (zero relationship writes; **`MATCH`-never-`MERGE` a target**, so a dangling link yields no edge — never re-introducing the placeholder-node bug [`ADR-storage-007`](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md) fixed); `search` delegates seeding/BFS/tie-break to a transient in-memory `GraphStore`, giving byte-identical id-set + order vs the baseline. `falkordb` is lazy-imported in `connect()` only, fail-loud.
- **PR2 — opt-in `native=True` (#200):** materialize the typed `[:REL {rel_type}]` graph from the `okf_links` SSOT in one pass, traverse with direction-keyed variable-length openCypher, and score by **importance-weighted multi-path accumulation** (`sum` over paths of `seed_score · reduce(per-rel weights)`). The default stays the parity floor, byte-for-byte (the 28 parity tests guard it).

Per the [`ADR-storage-005`](ADR-storage-005-dedup-on-write-default-off.md) / D019–D020 posture, the **accuracy claim was deferred to a captained measurement** — CI proves mechanism + headroom + no-regression only, never the live number.

## Options considered

- **Keep flat single-best-path decay as the only graph retrieval.** Rejected: it provably can't express convergent-path multiplicity — a real relational signal a node-DB can.
- **Make native the default graph path.** Rejected: native needs a real FalkorDB (a client dep + a server/embedded instance); the stdlib in-memory store must stay the zero-dependency offline default *and* the regression baseline. Native is **opt-in**.
- **Ship native opt-in, default to the parity floor, and measure the capability captained (chosen).** Bank a real, measured capability gain without changing the default or the offline floor.

## Decision

`FalkorGraphStore` ships as a parity floor (default) plus an opt-in `native=True` mode, and **the captained measurement confirms native beats the in-memory baseline on a structural divergence case.**

**The measurement (D059).** Run against a real **embedded FalkorDB** — `falkordblite` (ships the FalkorDB graph module inside a `redislite` embedded redis-server; installs into an isolated Python 3.12 venv, no Docker, no libomp issue) — via `FalkorGraphStore(client=<embedded>, native=True)`, reusing the shipped `CONVERGENT_HUB` fixture from [`test_falkor_native_eval.py`](../../eval/memeval/stores/tests/test_falkor_native_eval.py) (gold reached by **3** convergent `depends_on` paths, distractor by **1**, equal hop-distance, coined tokens):

| | in-memory baseline | native FalkorDB |
|---|---|---|
| convergent-hub @ k=1 | `[seed-zenith]` — **misses gold** | `[gold-apogee]` — **solves it** |
| full ranking | distractor sorts **ahead of** gold (flat decay ties them; timestamp breaks toward the distractor) | gold ranks first (3 paths > 1) |
| convergent edges stripped | (n/a) | `[relay-lumen]` — **also misses** → win is **structural**, not lexical |

Deterministic across reruns.

## Rationale

The convergent-hub case is **pure graph topology**: identical lexical seeds, identical hop-distance, the only difference is how many convergent paths reach the gold. Flat single-best-path decay ties gold and distractor; native multi-path accumulation separates them by counting the convergent paths. The **link-differential control** (strip the three convergent edges → native also misses the gold) proves the win comes from the graph structure, not from lexical overlap. So native expresses a retrieval signal the in-memory store *structurally cannot* — confirmed on a real FalkorDB, not a fake.

## Tradeoffs & risks

- **Capability proof, NOT a real-workload claim.** One constructed divergence case + one control. It shows native *can* win where the baseline structurally can't; it does **not** show native improves the actual SWE-Bench-CL / xarray benchmark. That requires wiring native into the live plugin path + a full pipeline run (below).
- **Opt-in + a real dependency.** Native needs the `falkordb` client and a FalkorDB instance; the stdlib in-memory parity floor stays the zero-dep offline default and the regression baseline. `falkordblite` (embedded) is the captain-friendly instance (a real graph module, so the Cypher/traversal is genuine).
- **The PR2 Cypher surface was verified against real FalkorDB** before this run: plain `CREATE INDEX` (no `IF NOT EXISTS` — unsupported), seed scores folded **client-side** (no `$map[key]` indexing), and **no `algo.*` procedure is load-bearing** (the scorer is pure Cypher `reduce` accumulation; `algo.pageRank` is global-only).
- **Risk window:** native is unexercised on the product path until it's wired in; the default floor is the safety net.

## Consequences for the build

- **Policy:** native graph retrieval is a **measured, real capability gain** on a structural case the in-memory store cannot solve. The **default graph path stays the parity floor**; native is opt-in (`native=True`).
- **Next (open):** to turn capability into a benchmark number, wire `native=True` into the live plugin path behind a graph profile in [`contract.build_store`](../../plugin/cookbook_memory/core/contract.py) (the harness seam — cross-team) + run the SWE-Bench-CL pipeline; and/or extend the divergence fixture to more case shapes (importance-weighted-distance, depth-reach) for a richer offline headroom set.
- **Affected files / evidence:** [`falkor_store.py`](../../eval/memeval/stores/falkor_store.py) (parity floor + `native=True`), [`test_falkor_native_eval.py`](../../eval/memeval/stores/tests/test_falkor_native_eval.py) (offline headroom + the `@skipUnless(FALKORDB_TEST_URI)` live cases), [`test_falkor_native_structural.py`](../../eval/memeval/stores/tests/test_falkor_native_structural.py) (native mechanism + id-set no-regression). Captained run via `falkordblite` + client-injection; matrix recorded in the captain's private scratch (D059).
- **Cross-links:** [`ADR-storage-007`](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md) (the parity-floor-then-native-upside pattern this mirrors; FalkorDB native is the FalkorDB analog of Neo4j Phase B), [`ADR-storage-006`](ADR-storage-006-typed-directional-graph-edges-okf-anchor-tuple.md) (the typed/directional `okf_links` the native graph materializes from), [`ADR-storage-003`](ADR-storage-003-router-profile-spectrum-fusion-default.md) (the router profile spectrum a native graph profile would slot into).
