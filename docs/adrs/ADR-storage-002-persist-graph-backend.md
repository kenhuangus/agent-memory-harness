---
id: ADR-storage-002
domain: storage
title: The graph backend is constructed with a durable path under $MEMORY_STORE, not in-memory
status: Proposed
date: 2026-06-22
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: design session 2026-06-22 (SWE-Bench-CL live-plugin pipeline — graph layer must persist for the shared substrate)
---

# ADR-storage-002: The graph backend is constructed with a durable path under `$MEMORY_STORE`, not in-memory

**Status:** Proposed · **Date:** 2026-06-22 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

`build_store()` in [`plugin/cookbook_memory/core/contract.py`](../../plugin/cookbook_memory/core/contract.py)
assembles the three routed backends from `$MEMORY_STORE` (`root`):

```python
root = Path(store_path); db_path = str(root / "memory.db")
...
backends = {
    "vectors":  vectors,                          # SqliteVectorStore(db_path)  -> $MEMORY_STORE/memory.db   (durable)
    "markdown": MarkdownStore(root / "markdown"), #                              -> $MEMORY_STORE/markdown/   (durable)
    "graph":    GraphStore(),                      # <-- NO path: pure in-memory, EVAPORATES on process exit
}
```

`vectors` and `markdown` persist under the store directory; **`graph` does not** — it is built
with no `path`, so it is the store's pure in-memory variant
([`GraphStore.__init__`](../../eval/memeval/stores/graph_store.py): `path=None` → RAM-only,
zero-dependency; `path=<file>` → a WAL-mode SQLite mirror loaded on construct). Each `claude`
turn is a fresh process, so the graph backend starts empty every time and loses everything it
learned when the process exits.

This was harmless while accumulation happened by harness-side copying of the *whole*
`.cookbook-memory` directory (the now-removed "Fix A"; the graph layer simply contributed
nothing). But [`ADR-eval-003`](ADR-eval-003-pipeline-shared-memory-substrate.md) makes the
store ONE shared substrate that accumulates across stages *by directory persistence*, and the
hypothesis under test is that this accumulating memory makes the agent smarter. A backend that
evaporates every process cannot participate. For the graph layer (typed OKF links — the
relationship structure between memories) to contribute to cross-stage learning, it must persist
like the other two.

## Options considered

- **Construct the graph with a durable path under the store root** (chosen):
  `GraphStore(path=str(root / "graph.db"))`. The graph then lives at
  `$MEMORY_STORE/graph.db` alongside `memory.db` and `markdown/`, and reloads on each process
  via the store's existing WAL-mode SQLite mirror.
- **Leave the graph in-memory.** Rejected: it silently contributes nothing to a persistent
  substrate; the routing profile lists three backends but only two actually accumulate, so the
  graph's contribution to the "does memory help over time" result is structurally zero — a
  misleading benchmark.
- **Persist the graph to Neo4j (the paid-path `uri=` seam).** Rejected for v1: heavy external
  dependency for a benchmark that must run offline/stdlib-first; the SQLite mirror is the
  designed durable path and is dependency-free.

## Decision

In `build_store()`, construct the graph backend with a durable path under the store root:

```python
-        "graph": GraphStore(),
+        "graph": GraphStore(path=str(root / "graph.db")),
```

`GraphStore.__init__` already accepts `path=` and, when given, opens a WAL-mode SQLite mirror
and loads it; no change to `GraphStore` itself is required. `root` already derives from
`$MEMORY_STORE`, so the graph DB lands at `$MEMORY_STORE/graph.db`, consistent with
[`ADR-dreaming-019`](ADR-dreaming-019-memory-store-is-a-directory.md) (everything lives under the
store directory) and the durable-mirror design the vector store already uses.

## Rationale

It is a one-line change that makes the three backends symmetric: all three persist under
`$MEMORY_STORE`, all three reload per process, all three accumulate across the pipeline's
stages. Without it the shared-substrate experiment is quietly measuring a two-backend system
while claiming three. The durable path is the store's own designed mechanism (the same WAL-mode
SQLite mirror as the vector store), so there is no new dependency and the offline/stdlib-first
guarantee holds.

## Tradeoffs & risks

- **Disk + a new file under every store.** `$MEMORY_STORE/graph.db` (+ WAL/SHM) now exists for
  every store. Negligible; consistent with `memory.db` already being there.
- **WAL fail-loud on a path that can't take WAL.** `GraphStore` raises if `journal_mode=WAL`
  doesn't take for a file-backed DB (same policy as `SqliteVectorStore`). Accepted — it is the
  intended fail-loud, and the store directory is a normal local filesystem.
- **Cross-process concurrency on the graph mirror.** With one shared substrate, concurrent
  `claude` turns could write the graph DB simultaneously. WAL allows concurrent readers + one
  writer; the pipeline runs plugin stages at `--plugin-workers 1` (MCP concurrency limit), so
  concurrent graph writers are not expected in practice. Deeper cross-process write coordination
  on `$MEMORY_STORE` is already flagged for the Dream mutation half in
  [`ADR-dreaming-020`](ADR-dreaming-020-cross-process-dream-mutation-gate.md); this ADR does not
  change that gate.
- **Existing in-memory-graph results.** Any prior run implicitly excluded the graph layer; new
  runs include it, so graph-influenced retrieval may differ. Accepted — that is the point — and
  the version bucket (ADR-eval-004) separates generations.

## Consequences for the build

- **Policy:** `build_store()` constructs all three backends with a durable location under
  `$MEMORY_STORE`. No backend in the default (offline/fusion/accuracy) profiles is RAM-only.
- **Shape:** `$MEMORY_STORE/graph.db` (+ `-wal`, `-shm`) joins `$MEMORY_STORE/memory.db` and
  `$MEMORY_STORE/markdown/` as the persistent store layout.
- **Affected files:** `plugin/cookbook_memory/core/contract.py` (the one-line change); a test
  asserting `graph.db` appears under the store directory after a write through `build_store()`.
- **No change to `GraphStore`:** the `path=` seam already exists; this ADR only exercises it in
  the plugin's store assembly.
- **Prerequisite:** this must land before the pipeline's accumulation stages are trusted, or the
  graph layer's contribution to cross-stage learning is silently zero.
