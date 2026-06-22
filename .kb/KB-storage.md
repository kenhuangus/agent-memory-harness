# Knowledge Base — storage

**Domain owner:** Brent
**First entry:** 2026-06-22

Append-only journal of project-story snapshots for the **storage** workstream.
See [README.md](README.md) for conventions.

---

## 2026-06-22T11:32 — entry 1

**Triggered by:** Initial KB seeding via cross-cutting `/kb all` run — establishes baseline state of the storage workstream as the `.kb/` convention lands in the repo.
**Branch:** harness/add-kb-command
**Related ADRs:** ADR-storage-001
**Cross-domain run:** [KB-harness.md](KB-harness.md), [KB-dreaming.md](KB-dreaming.md), [KB-eval.md](KB-eval.md)

### Summary
The storage workstream owns the persistence + retrieval seam — the `MemoryStore` backends under `eval/memeval/stores/` and the `Router` that decides which store a write goes to and which a read comes from. Since the contract freeze, the focus has been turning the scaffolded stubs into something the eval can actually beat the no-memory baseline with: a SQLite-backed vector store with proper concurrency (WAL pragma per ADR-P2, PRs #52/#55), a Voyage real-embedder adapter behind the embedder seam (PR #41, PR3b-1), a semantic router classifier wired to exemplar-NN (PR #49, PR3b-2), and write-side routing with off-by-default dedup (PRs #56/#57). The orchestrator decision — in-process library, no daemon, keyed by `$MEMORY_STORE` (ADR-storage-001) — has held; everything new wires through that one seam.

### Key state
The router is the **only** path between an agent and a backend store. It now handles both reads (PR #44's D019/D020 semantic-retrieval eval fixture proved headroom for a real embedder) and writes (PR #56's `Router.route_write` for D009/D023, PR #57's dedup-on-write for D024 — dedup is off by default because offline lexical dedup is unsafe, an explicit decision worth flagging). The classifier bake-off (PR #34, PR3a) settled the routing approach before any model integration; PR #29's cheap-fix rules resolved the 9 GAP:cheap-fix misses against the 42-case blind multi-lens routing eval (PR #28). The frozen contract (`schema.py`, `protocols.py`) still gates every backend — new stores implement the protocol in their own file under `stores/`, no contract edit.

### Open items
- Dedup is off by default and the team has accepted that posture for the offline path. The "when do we turn it on" question depends on whether a real embedder ships before the sprint ends; without semantic similarity, lexical dedup costs recall.
- The SemanticRouterClassifier currently uses exemplar-NN with an injected encoder seam — the encoder is mock-tested but not yet wired to Voyage in the routing path; that's the natural next step once the embedder seam from PR #41 stabilizes.
- The `InMemoryStore` reference stub is the only backend guaranteed to work without dependencies; the SQLite path requires the file-backed concurrency guarantees from PRs #52/#55. Markdown store coverage is partial.

### Artifacts at time of entry
- [`architecture.md`](../architecture.md)
- [`prd.md`](../prd.md)
- [`plan.md`](../plan.md)
- [`docs/adrs/ADR-storage-001-orchestrator-in-process-library.md`](../docs/adrs/ADR-storage-001-orchestrator-in-process-library.md)
- `eval/memeval/router.py`
- `eval/memeval/stores/` — backends + embedders + tests
