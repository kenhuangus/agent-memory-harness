---
id: ADR-storage-009
domain: storage
title: Backend durability audit — both durable backends rate needs-hardening (markdown/OKF ≈ POC persistence); a hardening arc is queued
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D040 (capstone-workspace); BACKEND_DURABILITY_AUDIT.md / REMEDIATION_PLAN.md
---

# ADR-storage-009: Backend durability audit — both durable backends rate `needs-hardening`; a hardening arc is queued

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

[`architecture.md`](../../architecture.md):218-223 presents all the store backends (`memory.db`, `markdown/`, `graph.db`) as durably persisting — a **settled fact**, cited to [`ADR-storage-002`](ADR-storage-002-persist-graph-backend.md). Before building a *new* durable backend (Neo4j, [`ADR-storage-007`](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md)), Brent asked to double-check that the **existing** durable backends can actually perform under the real load model — *"if either was built just to show proof of concept, we'll circle back and harden."*

The audit (D040) ran as a Workflow: 8 review lenses (2 stores × durability/concurrency/performance/architecture) → **each finding adversarially verified against source** (verifiers empirically SIGKILL'd the store mid-write-burst and ran 2–4 concurrent OS processes against the same directory) → per-store synthesis. Several reviewer claims were **refuted by execution** and filtered out — the verdict is graded, not raw.

The **load model** both backends are judged against is real, not hypothetical: at runtime an MCP recall/remember path AND a background Daydreamer both read/write the SAME `$MEMORY_STORE` directory; both backends are live on the product path (`_Engine.remember` fans out via `write_policy=base_all`, so every `remember` writes a markdown OKF doc and a sqlite row).

## Options considered

- **Treat durability as solved (the doc's current framing).** Rejected: the audit empirically shows one *live* backend can lose data under the real load model — leaving the claim as settled fact is a stale, misleading claim, not a harmless omission.
- **Deviate now and harden before building Neo4j.** Rejected: would stall the in-flight graph/Neo4j track. The findings are recorded durably and the hardening is *scheduled*, not dropped.
- **Record the verdict + queue a hardening arc immediately after the graph thread (chosen).** Stay on the Neo4j track; capture the known-risk so it can't slip; HIGHs first, eval-first (the markdown store has no crash/concurrency test today — that instrument is step 0).

## Decision

Record the audit verdict as a **known durability risk** and queue a **Backend Durability Hardening Arc** to run immediately after the graph store/Neo4j thread completes.

**Verdict: both existing durable backends rate `needs-hardening`.**

- **`SqliteVectorStore`** — durability core is **genuinely production-grade**: WAL fail-loud, fsync'd commits, 5s busy_timeout, crash- + multi-process-verified (cross-process lost-update closed by [`ADR-dreaming-021`](ADR-dreaming-021-dream-mutation-concurrency.md)'s basedir flock). Gaps are minor: 1 MED (thread-affine `self._conn` crashes if handed to a thread pool — the non-default `run_agent(workers>1)` path) + 2 LOW (write rollback asymmetry; O(N) per-row materialization on recall).
- **`MarkdownStore`/`OKFStore`** — **effectively POC persistence**, with **three HIGH issues on the LIVE path**: (1) **non-atomic `write_text`** ([`okf.py`](../../eval/memeval/okf.py):400) — a torn/empty `.md` is silently dropped on next autoload, **destroying the prior good copy** on an update; (2) **no cross-process lock + a RAM mirror frozen at `__init__`** — last-writer-clobber + stale/split-brain recall under live MCP+Daydreamer load; (3) **O(N) full-rescan `delete()`** → quadratic under dedup bursts. Plus 4 MED. **The fix primitives (`tmp.replace`, `fcntl.flock`) already exist in-repo** ([`dreaming/_state.py`](../../eval/memeval/dreaming/_state.py):240-257, 290-322; ADR-013/014) → a port, not a rewrite.

## Rationale

The doc currently misrepresents durability as solved; the audit — graded by *empirical* crash/multi-process verification, not static review — shows the already-live markdown backend can silently lose a memory on a torn write and serve stale recall under the real concurrency model. Building a *new* durable backend (Neo4j, with no live consumer and a no-op-on-accuracy Phase A) while an *already-live* backend loses data would invert the "accurate on writes AND retrievals" bar — so the honest move is to record the risk durably and schedule the port. Staying on the graph track now (rather than context-switching) is right because the fixes are a focused primitive-port the dreaming module already proved, not a rewrite, and they sequence cleanly after the graph thread.

## Tradeoffs & risks

- **The risk window stays open until the arc runs.** Between now and the hardening arc, the markdown/OKF backend retains the three HIGH live-path data-loss/stale-recall failure modes. Accepted, time-boxed (arc queued at the top of [`REMEDIATION_PLAN.md`](../../REMEDIATION_PLAN.md)); the deferral is explicit, not silent.
- **No crash/concurrency test exists for the markdown store today.** The verdict rests on the audit's empirical verification, not a committed regression test — so the failure modes are not yet guarded in CI. Step 0 of the arc is exactly that instrument (eval-first).
- **Recording a `needs-hardening` verdict reads as "not done."** It is — for the markdown backend's *production* durability. That honesty is the point: the durable-persistence claim must carry this caveat until the arc lands.

## Consequences for the build

- **Policy / known-risk:** the durable-persistence claim is **qualified** — `SqliteVectorStore`'s core is production-grade; `MarkdownStore`/`OKFStore` is POC-grade persistence with three HIGH live-path risks. Do NOT treat markdown durability as production-ready under concurrent MCP+Daydreamer load until the hardening arc lands.
- **Policy:** the **Backend Durability Hardening Arc** runs immediately after the graph store/Neo4j thread, HIGHs first, eval-first (crash/concurrency instrument = step 0), porting the existing `tmp.replace`/`fcntl.flock` primitives.
- **Affected files / evidence:** [`BACKEND_DURABILITY_AUDIT.md`](../../../BACKEND_DURABILITY_AUDIT.md) (full findings, verdicts at a glance), [`REMEDIATION_PLAN.md`](../../../REMEDIATION_PLAN.md) (queued arc); fix sites `okf.py:400` (atomic write), `okf.py:392-394,402-409` + `markdown_store.py:58-59` (flock + RAM-mirror refresh), `okf.py:411-433` (O(N) delete); fix primitives in `dreaming/_state.py:240-257,290-322`.
- **Cross-links:** the durability claim this qualifies is [`ADR-storage-002`](ADR-storage-002-persist-graph-backend.md) (graph `path=` seam) and architecture.md:218-223; the basedir flock that already closed the sqlite cross-process lost-update is [`ADR-dreaming-021`](ADR-dreaming-021-dream-mutation-concurrency.md); the new backend this audit gates ahead of is [`ADR-storage-007`](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md).
- **Doc-reconciliation owed:** architecture.md §7.4 — replace the unqualified "all three backends persist" framing with the audit caveat (markdown/OKF needs-hardening) + a pointer to this ADR and `BACKEND_DURABILITY_AUDIT.md`.
