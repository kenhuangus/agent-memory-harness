---
id: ADR-storage-007
domain: storage
title: Neo4jGraphStore — a fourth backend over the Bolt driver, Phase-A parity floor (nodes + okf_links SSOT; native typed graph deferred to Phase B)
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D039/D041 (capstone-workspace); PR #111
---

# ADR-storage-007: `Neo4jGraphStore` — a fourth backend over the Bolt driver, Phase-A parity floor

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

The graph store's `uri=` seam ([D014]; [`graph_store.py`](../../eval/memeval/stores/graph_store.py):132) was always reserved for a real typed-edge graph DB (Neo4j) as the paid-path upgrade of the in-memory `GraphStore`. Building it makes a **fourth** storage backend — directly contradicting architecture.md's repeated *"three indexed storage backends"* / *"all three store backends"* framing (`:11`, `:220-222`). Two decisions had to be made: the **transport** and the **build strategy**.

**Transport (D039).** The fork was framed as *"`neo4j` driver vs raw Bolt"* (wrong axis — the driver *is* how you speak Bolt). The real fork: **(A) Bolt via the official `neo4j` driver** vs **(B) Neo4j's HTTP Query API over stdlib `urllib`**, which would mirror `VoyageEmbedder`'s no-SDK/`urlopen`-mock discipline exactly. I argued for (B) — zero new dependency, reuses the existing mock, most consistent with the codebase ethos. **Brent corrected the framing:** the "runs once, transport efficiency is irrelevant" reasoning applies only to the captained *parity validation*; the **backend itself, if used, calls the driver on every write/delete/search under real load** — it is a load-bearing backend, not a parity-only artifact.

**Build strategy (D041).** A graph-DB port can silently *regress* or silently *gain* accuracy; you can't tell which without a baseline. So Phase A is a **parity FLOOR**: Neo4j must reproduce the in-memory `GraphStore`'s retrieval **id-set + order EXACTLY** before earning the right to improve them.

## Options considered

- **Transport (B) HTTP Query API over `urllib`.** Zero dependency, reuses the `urlopen`-monkeypatch mock. **Rejected:** under real per-call load, hand-rolling connection pooling, managed-transaction retry, transactions, and cluster routing over `urllib` (no keep-alive → TCP+TLS+auth handshake per call) is a worse reimplementation of what the Bolt driver gives for free.
- **Transport (A) Bolt via the `neo4j` driver (chosen):** pooling, retry, transactions, `neo4j://` cluster routing — recurring wins for a backend that hits the driver on every mutation/read.
- **Build: reimplement seed→BFS→score in Cypher.** Rejected — reopens the float/order divergence the scope doc warns about; that's explicitly Phase B's job.
- **Build: Phase-A parity by delegation (chosen)** — `search` pulls as_of-visible nodes via Cypher, builds a **transient in-memory `GraphStore`**, and delegates scoring/BFS/tie-break to it. Parity *by construction* — it cannot drift.
- **Build: write the native typed `[:REL]` graph in Phase A.** **Tried and DROPPED mid-flight (the Codex R1 blocker):** a per-edge `MERGE (b:Memory {item_id:$tgt})` *creates* the target on a real Neo4j, so a forward-reference link materializes a read-visible **placeholder** node (id-set divergence) and a later real write `MATCH`es the placeholder so `ON CREATE SET n.seq` never fires (broken `all()` order). The offline `FakeBoltDriver` structurally masked it.

## Decision

Ship **`Neo4jGraphStore`** ([`stores/neo4j_store.py`](../../eval/memeval/stores/neo4j_store.py)) — a **fourth** backend behind the `uri=` seam — over the **official `neo4j` Bolt driver** (D039). **Phase A is a parity floor (D041):** it persists `:Memory` **nodes + their `okf_links`** (the durable edge SSOT) only; `search` delegates scoring/BFS/tie-break to a **transient in-memory `GraphStore`** for exact id+order parity. **Native typed `[:REL]` writes are DROPPED from Phase A** and deferred to **Phase B** (materialize the native graph from the complete `okf_links` SSOT in one pass — `MATCH` endpoints, never `MERGE` — then native Cypher/GDS traversal, captained-measured). `neo4j` is **lazy-imported inside `connect()` only** (never at module load — `VoyageEmbedder` discipline), **fails loud** on a set `uri` with no driver, and the **offline default is untouched** (in-memory `GraphStore`; `neo4j` never enters `sys.modules` on the CI path). Parity is verified offline against a committed `FakeBoltDriver` (28 tests) that **models Neo4j endpoint-creation** so the placeholder bug can never sneak back; the live `NEO4J_TEST_URI` parity run is the owed captained step.

## Rationale

Bolt is right because the backend runs under load and the driver's per-call machinery (pooling, retry, cluster routing) are recurring wins that re-implementing over `urllib` would only approximate worse — Brent's "called on every mutation, must run under load" reframing flipped the cheaper-looking path that would have under-built a load-bearing backend. Parity-by-delegation is right because reusing the in-memory scorer *guarantees* identical ids+order — a reimplementation can't make that promise, and a port you can't prove faithful is worthless as a regression guard. Dropping the Phase-A `[:REL]` writes costs nothing (Phase-A reads use only `okf_links`) and removes a real-Neo4j correctness bug; the `okf_links` SSOT lets Phase B build the complete native graph later with no re-ingest. The whole episode is the cleanest case for **gating a self-authored fake with a different vendor**: the offline suite passed 28/28, and a cross-vendor reviewer caught the `MERGE` placeholder bug the fake had no way to surface.

## Tradeoffs & risks

- **A new optional dependency (`neo4j`).** Mitigated by strict lazy import + fail-loud: the package never imports `neo4j` at load, the offline/CI path is byte-identical, and a set `uri` with no driver fails loudly rather than silently degrading.
- **Phase A is a no-op on accuracy.** It only proves the port is faithful — the accuracy upside is Phase B. Accepted by design: a parity floor must exist before native-graph gains can be told apart from silent regressions.
- **The only executable proof is a self-authored fake.** The `FakeBoltDriver` can mask real-Neo4j semantics (it did — the placeholder bug). Mitigations: the fake now **models endpoint-creation** (so a reintroduced rel write fails parity), and the live `NEO4J_TEST_URI` captained run remains **owed** before Cypher validity against a real DB is claimed.
- **The doc says "three backends."** A reader of architecture.md does not know a Neo4j backend exists. Doc-reconciliation owed (`:11`, `:220-222`).

## Consequences for the build

- **Policy:** the durable backend count is **four** (markdown, vectors, in-memory graph, Neo4j graph). `Neo4jGraphStore` is the `uri=` upgrade of `GraphStore`; selecting it must keep the offline default untouched and fail loud if `neo4j` is missing.
- **Policy (Phase boundary):** Phase A persists **nodes + `okf_links` only** and delegates retrieval to a transient `GraphStore`. Phase B materializes the native `[:REL]` graph — it must **`MATCH` endpoints, never `MERGE` a target** (that is exactly the R1 placeholder bug). Do not reintroduce Phase-A rel writes (the parity tests will fail).
- **Affected files:** [`stores/neo4j_store.py`](../../eval/memeval/stores/neo4j_store.py) (the backend), [`stores/__init__.py`](../../eval/memeval/stores/__init__.py) (`__all__` + lazy `Neo4jGraphStore` export :29/:42-47); tests `test_neo4j_parity.py` (28, offline), `test_neo4j_live_parity.py` (opt-in `@skipUnless NEO4J_TEST_URI`).
- **Cross-links:** the in-memory graph this upgrades is [`ADR-storage-006`](ADR-storage-006-typed-directional-graph-edges-okf-anchor-tuple.md); its durability SSOT is [`ADR-storage-002`](ADR-storage-002-persist-graph-backend.md); the durability hardening of the *existing* backends is [`ADR-storage-009`](ADR-storage-009-backend-durability-audit-hardening-arc.md).
- **Owed:** the captained live `NEO4J_TEST_URI` run (validates Cypher against real Neo4j — flips the live test's captained-pending flag). Phase B (native typed-graph + GDS accuracy, captained) is the scoped follow-on, NOT this ADR.
- **Doc-reconciliation owed:** architecture.md — "three" → "four" backends; record the Neo4j `uri=` backend, Bolt transport, and the Phase-A/Phase-B split.
