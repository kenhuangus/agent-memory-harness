---
id: ADR-dreaming-020
domain: dreaming
title: v2 Dream mutation half is gated on a successor ADR resolving cross-process concurrency on `$MEMORY_STORE`
status: Superseded
date: 2026-06-22
contract: true
supersedes: none
superseded_by: ADR-dreaming-021
owner: Scott B. (P4)
origin: eval/memeval/dreaming/tests/INITIAL_DREAM_RUBRIC.md §L (jasnah + halliday review of the Dream v1 worker)
---

# ADR-dreaming-020: v2 Dream mutation half is gated on a successor ADR resolving cross-process concurrency on `$MEMORY_STORE`

**Status:** Superseded by [ADR-dreaming-021](ADR-dreaming-021-dream-mutation-concurrency.md) · **Date:** 2026-06-22 · **Contract:** yes
**Supersedes:** none · **Superseded by:** [ADR-dreaming-021](ADR-dreaming-021-dream-mutation-concurrency.md)

> **What this ADR does.** It does **not** pick the v2 concurrency model. It
> **names the gate** that the v2 mutation PR cannot ship without resolving. v1
> (detection-only) is safe by construction; v2 (item retirement/merge) inherits
> a real race that none of today's primitives cover, and the rubric flagged it
> as a v2 blocker. This ADR makes the gate a contract: any PR that turns the
> Dream worker's mutation path on must cite this ADR and reference a successor
> ADR that picks one of the three options below (or a fourth) with eyes open.

## Context

The first substantive Dream worker landed today as **detection-only** (Job 1
dedup). v1 walks `store.all()`, clusters near-duplicates, and emits an event —
**zero writes**. That is the only reason it is safe under concurrency: two
`daydream-cli dream --all` processes running against the same
`$MEMORY_STORE` produce two reports, never two conflicting writes.

v2 — already on the dream-consolidation roadmap — adds the **mutation half**:
once a cluster of near-duplicates is detected, one item wins and the others
are *retired* (and a consolidation pointer is written back, depending on
shape). This is where the v1's read-only safety property evaporates.

Four facts make this a real choice, not a default:

1. **`MemoryStore` has no `delete`.** The protocol exposes `write` /
   `get` / `search` / `all` only
   ([`eval/memeval/protocols.py`](../../eval/memeval/protocols.py)). `write` is
   idempotent on `item_id`; `version` is the conflict-resolution field. There
   is no `tombstone`, no `retire`, no `delete` — retirement today can only be
   expressed as a *write* (e.g. `relevancy=0.0` on the loser, or a new
   `tombstone: bool` field after a contract change). v1's docstring
   explicitly defers this:
   `eval/memeval/dreaming/worker.py:9-13` —
   *"there is no `delete` method, so cross-session near-duplicates with
   different `item_id` values cannot be retired inside the protocol. The
   mutation half is a follow-up PR after the delete/tombstone contract is
   settled."*

2. **`Router.write` already does dedup-on-write**, controlled by
   `RouterConfig.dedup`
   ([`eval/memeval/router.py`](../../eval/memeval/router.py)). When a near-duplicate is
   found, the existing `item_id` is reused and `version` is incremented. This
   is the closest thing to a CAS-like primitive that exists today, but it is
   *not* version-conditional — it is "find a duplicate, merge, bump." Two
   concurrent dream-cli processes both observing the same pre-merge state can
   both perform the merge, and the protocol does not currently reject the
   stale one.
   **Note (load-bearing for Option B below):** `RouterConfig.dedup` defaults
   to `False` and the Router code (`router.py:471-481`) explicitly flags
   offline auto-merge as `FALSE MERGES = silent data loss` — the offline
   char-n-gram embedder cannot separate near-dups from distinct-but-similar
   memories. The Dream sweep runs offline; Option B therefore must decide
   whether CAS rides on top of dedup-on (overturning the current safety
   posture) or replaces it as the conflict-resolution primitive.

3. **Today's concurrency primitive does not cover this.**
   [`ADR-dreaming-014`](ADR-dreaming-014-concurrent-daydream-flock.md) defines
   a **per-`session_id` non-blocking `flock`** at
   `<basedir>/dream/<session_id>.lock`. That primitive guards same-session
   Daydream races. Dream is *whole-store*, has **no `session_id` scope**, and
   per [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md) is
   invoked deliberately by **the eval pipeline between batches AND by a
   human** — exactly the two-writer shape the per-session lock was never
   designed to guard. Two `dream --all` processes against the same
   `$MEMORY_STORE` today take *no* lock against each other.

4. **The rubric named this as a v2 blocker.**
   [`eval/memeval/dreaming/tests/INITIAL_DREAM_RUBRIC.md`](../../eval/memeval/dreaming/tests/INITIAL_DREAM_RUBRIC.md) §L:
   *"Explicit gate for v2: before the mutation half of the dream worker
   ships, an ADR must address multi-process dream-cli dream --all against the
   same MEMORY_STORE. v1 is safe because writes are zero; v2 inherits this
   gap unless the ADR pins it first."* Jasnah and halliday both flagged it
   during the v1 review.

### Two race shapes the successor ADR must cover

**Shape 1 — Dream-vs-Dream (same writer class).** Worker A reads the store,
picks loser items `{x, y}` to retire under merge `M_A`. Worker B reads the
same snapshot 0ms later, independently picks `{x, z}` to retire under merge
`M_B`. Both compute version-bumps; both call `Router.write`. The store ends
with two consolidation merges over an overlapping source set, and the
non-overlapping victims (`y`, `z`) are retired against the wrong winner.
Dedup-on-write does not catch this — `M_A` and `M_B` have different content
hashes because clustering is non-deterministic across embedding-cache
states. The defect surfaces as inflated consolidation counts and double-
attribution in `recall` traces; on a noisy benchmark it looks like signal.

**Shape 2 — Daydream-vs-Dream (cross-writer-class; the harder shape).** A
Stop-hook-fired Daydream pass (ADR-001) writes a new memory item mid-Dream-
sweep. The Dream sweep's `store.all()` snapshot is now stale; it may retire
a winner the Daydream just merged into, leaving the Daydream's new content
attached to a retired/zeroed-relevancy item. The single-mutation-surface
assumption Option A relies on is **already false** by virtue of Daydream
being a concurrent writer — it just happens to be on a different code path.
The successor ADR must decide whether the gate is "Dream sweeps serialize
against each other" (Shape 1 only) or "all writes against `$MEMORY_STORE`
serialize during a Dream sweep" (Shape 1 + Shape 2 — much heavier). Brent
and Keith need to see this race shape named before they can sign off on the
single-mutation-surface invariant in Option A or the CAS-aware-callers
audit in Option B.

## Options considered

These were drafted and adversarially reviewed during the v1 ship review
(jasnah + halliday). Summarized here by name; the successor ADR picks one
(or proposes a fourth) and inherits the named amendments.

- **Option A — Cross-process `flock` on `<basedir>/.dream.lock`** (lift
  ADR-014's primitive from session scope to basedir scope; single-writer
  serialization, non-blocking, exit-0 silent skip).
  *Verdict from review:* **DEFENSIBLE-WITH-CAVEATS / SURVIVES-WITH-AMENDMENT.**
  Mandatory amendments before adoption: (a) write-merge + version-bump
  must be a **single atomic journal entry** (replayed-or-rolled-back on
  next startup before clustering) — flock guards concurrency but not
  crash-atomicity, and the dedup-absorbs-duplicates story does *not*
  cover a partial version-bump sweep; (b) **hard-fail (not skip)** when
  `$MEMORY_STORE` is detected on NFS/SMB — basedir-scope locks on a
  network FS are a strictly larger blast radius than session-scope and
  the silent-corruption mode is unacceptable; (c) Ken signs off that
  `dream.lock_contended` is acceptable measurement loss at planned eval
  concurrency, or v2 ships with retry-loop semantics from day one;
  (d) the single-mutation-surface assumption is named as a load-bearing
  invariant Brent and Keith must check before adding any new writer —
  **and must explicitly address Shape 2 (Daydream-vs-Dream)**, since
  Daydream is already a concurrent writer on the same store; the
  successor ADR must decide whether Daydream also waits on the basedir
  lock during a Dream sweep, or whether Shape 2 is accepted as
  out-of-scope with a documented surface area.

- **Option B — CAS at the Router/Store layer** (Brent ships
  `cas_version: int | None` on `Router.write` and `MemoryStore.write`;
  retirement is a *write* with the prior `version` checked; multi-writer
  concurrency, no locks). *Verdict from review:*
  **DEFENSIBLE-WITH-CAVEATS / SURVIVES-WITH-AMENDMENT.** Mandatory
  amendments before adoption: (a) Brent commits to `cas_version` on
  **both `read` and `write`** across every backend (sqlite, markdown,
  graph) via a `[CONTRACT]` PR — partial-backend CAS is a lie on
  multi-backend routes; (b) **every** existing mutation caller (the
  Daydream Stop-hook write path included) is audited as CAS-aware in
  the same `[CONTRACT]` PR — partial deploys where one writer is
  version-blind silently resurrect retired items; (c) **semantic
  divergence** (workers seeing different store snapshots, computing
  different retire sets) is handled by **sharding retire work by a hash
  of `normalized_key` modulo a coordinated worker-count** so that any
  given cluster's retire decisions are owned by exactly one worker —
  the worker only acts on clusters whose key-hash falls in its
  assigned shard, and CAS catches the residual race where a Daydream
  write changes a cluster's `version` mid-retire. CAS protects races,
  not snapshot divergence; sharding is what neutralizes the divergence;
  (d) Ken signs off on replay-determinism for versioned items;
  (e) **name whether CAS turns Router dedup-on for the Dream path**
  (and defends against the offline false-merge risk the Router comment
  at `router.py:471-481` names) **or replaces it as the conflict-
  resolution primitive** — the successor ADR cannot leave both modes
  alive.

- **Option C — Single-writer leadership via a lease file** (atomic
  rename of `<basedir>/.dream.lease` with `{pid, uuid, renewed_at}`;
  heartbeat thread renews every 60s; `lease_max_age` reclaim on stale).
  *Verdict from review:* **KILL.** Two independent reviews killed it:
  (a) the acquisition protocol's read-then-rename sequence is not
  atomic — two concurrent acquirers on the same host can both pass the
  read-after-write check during a brief window where the loser's UUID
  is briefly on disk, violating the single-writer invariant on the
  *supported* deployment, not an NFS edge; (b) the lease adds a
  stateful heartbeat thread + time-mocking test infrastructure +
  operator runbook tuning to defend an NFS scenario ADR-014 already
  declared out of scope, and is strictly weaker than `flock`'s
  kernel-enforced semantics for cooperative workers. Option A
  dominates within the declared scope.

A fourth option — **defer mutation indefinitely; keep Dream as a
detection-only reporter that emits "this cluster should be merged"
events and lets a human or the next ADR-driven sweep apply them** —
is implicit in the gate and is the v2 successor ADR's prerogative to
choose if A and B both fail their amendments cost-benefit.

## Decision

**The v2 Dream worker mutation PR is BLOCKED on a successor ADR.**
Specifically:

1. No PR may flip the Dream worker from detection-only to detection+
   mutation without **citing this ADR as a precondition** and naming
   the successor ADR (`ADR-dreaming-NNN`) that resolves it. CI is not
   the enforcement here — the contract row in `docs/adrs/README.md` is.

2. The successor ADR must:
   - Pick **Option A, Option B, or a justified fourth option** (Option
     C is killed by review and may not be re-proposed without new
     evidence overturning both review findings).
   - **Honor the named amendments** for whichever option is chosen
     (atomic journal + NFS hard-fail + Ken sign-off + Shape-2-decision
     for A; CAS on read+write + all-writers-audited + key-sharded
     retire + Ken sign-off + dedup-vs-CAS pick for B).
   - Answer the four Open items below explicitly. An empty answer
     means the decision wasn't made.

3. **Recommended default direction: Option A (`flock` at basedir
   scope) with all four amendments.** This is opinionated, not
   prescriptive — the successor ADR may choose B with rationale, but
   it must defend the choice against this default. Rationale for the
   default:

   - **Cross-domain cost is zero.** No protocol change, no Router
     change, no eval-contract change. Pure dreaming-domain
     addition. Option B requires Brent to ship a `[CONTRACT]` PR
     across every backend before the dreaming workstream can move,
     which couples our v2 schedule to storage's roadmap.
   - **Consistent with the existing primitive.** ADR-014 already
     established `flock` as the in-house concurrency primitive at
     a smaller scope. Lifting it to basedir scope is the smallest
     concept-count increase.
   - **Single-machine local-FS is the only deployment we've
     committed to.** ADR-014 explicitly disclaims NFS;
     [`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)
     assumes single-machine SQLite (WAL on NFS is unsafe regardless
     of concurrency model). Option A's hard ceiling matches the
     deployment ceiling we already accepted.
   - **Reversibility is honestly cheap.** The lock file is private
     to `dreaming/`; replacing it with B (CAS) is additive — the
     CAS contract lands, the lock comes out. The event-stream
     `dream.lock_contended` is a one-time migration cost for Ken,
     not a multi-domain Big Bang.

   The recommendation is conditional on the four Option-A amendments
   landing in the same PR as the lock — particularly the atomic
   write-merge + version-bump journal and the Shape-2 decision. The
   default *without* the amendments is **not** Option A; it is
   "block v2 until the amendments are designed."

4. **The v1 detection-only worker is unaffected.** v1 has no
   mutation, takes no lock against other dream-cli processes, and is
   safe under concurrency by construction.

## Rationale

A forward-defense ADR now beats deciding-and-deferring because the
three viable answers each have **load-bearing cross-domain dependencies
that we don't yet have signed**:

- Option A is load-bearing on a journal/atomicity design that hasn't
  been written and a deployment-shape commitment (no NFS) we haven't
  formally pinned beyond ADR-014's footnote.
- Option B is load-bearing on a `[CONTRACT]` PR Brent has not
  scoped, a partial-deploy audit nobody has done, and an eval-replay
  determinism story Ken has not weighed in on.
- Option C is killed; surfacing this as a kill-with-reasons in an ADR
  prevents the same shape being re-proposed in three weeks under a
  different name.

Without this gate, the v2 PR ships, the reviewer sees clustering
detection works, hits "approve," and the mutation race lands silently.
The gate forces the v2 author to either own one of the three
amendments-bundles or document why they don't apply — both outcomes
are cheaper than discovering the race in benchmark noise.

Forward-defense is also the **honest** ADR shape for this moment:
naming the unanswered question is a real decision (we are committing
to not letting v2 ship without it), even when the *technical* answer
isn't ready. The alternative — "we'll figure it out in the v2 PR" —
relies on a reviewer remembering this conversation, which is exactly
the failure mode ADRs exist to defeat.

## Tradeoffs & risks

- **Cost of not picking the model now: gate friction without
  progress.** The v2 PR author will hit a "must write an ADR first"
  block, which adds ~1-2 days to the v2 schedule. Accepted: that
  cost is dominated by the cost of a silent mutation race shipping
  into benchmark runs. The gate cost is paid in design hours; the
  race cost is paid in re-running the entire eval suite plus
  reputation.

- **Risk: the successor ADR re-litigates rather than resolves.**
  The three options + adversarial review are summarized here so the
  successor ADR starts from a baseline, not from scratch. If the
  successor author wants to revisit Option C, the named kill-
  conditions (concurrent-acquirer non-atomicity; weaker trust model
  than flock) are the bar to clear, not an open question.

- **Risk: the recommended default (Option A) is chosen reflexively
  without the amendments.** Mitigation: the four Option-A
  amendments are named in the Decision section as **non-optional**,
  not "nice to have." A PR that lands the basedir flock without the
  atomic journal or without a Shape-2 decision must be rejected at
  review, not "tracked as a follow-up."

- **Risk: Brent ships `cas_version` on the storage roadmap before
  the dreaming v2 successor ADR exists.** That would make Option B
  cheap by accident and could pressure the choice. Acceptable: if
  Brent ships CAS, the successor ADR picks B with rationale; this
  ADR doesn't prejudge it, it just refuses to pretend the choice
  doesn't matter.

- **Risk: the gate becomes stale.** If the v2 mutation work is
  deferred past the bootcamp sprint, this ADR sits as a check on a
  PR that never comes. Accepted: the ongoing cost is small but
  non-zero — the ADR occupies the decision index and future ADR
  authors must reason past it during dependency-checks. The
  rubric language makes its re-discovery free, and a stale gate is
  cheaper than a silently-violated one.

- **Risk: a fourth option (defer mutation, ship detection-only as
  the durable Dream contract and let humans apply suggestions) is
  silently chosen by inaction.** Acceptable: that *is* the v1
  state. If we choose it deliberately, the successor ADR documents
  it as the chosen option and revisits when the eval pipeline
  demands automatic mutation. The forbidden shape is "we'll add
  mutation later" without an ADR.

## Consequences for the build

- **Contract — source of truth:** this ADR. The shape is the
  decision rule itself, not a code surface: *any PR that adds
  mutation to the Dream worker MUST cite a successor ADR that
  resolves cross-process concurrency on `$MEMORY_STORE`.*

- **Shape (the gate as enforceable rule):**
  - The Dream worker `eval/memeval/dreaming/worker.py` may write
    *only* through paths that the successor ADR has cleared.
  - Today, the cleared-write set is **empty**. The v1 worker is
    detection-only by design.
  - A PR that adds `Router.write(...)` or `store.write(...)` calls
    on the Dream code path is a contract violation against this ADR
    unless the cited successor ADR explicitly authorizes it.

- **Policy — `[CONTRACT]` PR process applies.** Per CLAUDE.md, the
  successor ADR's decision will touch `Router.write` semantics
  (Option B) or `<basedir>` layout + the events stream (Option A);
  either way the v2 PR loops the affected domain owners (Brent for
  storage, Ken for eval, Keith for harness if the Stop hook ever
  fires Dream).

- **Policy — exhaustive consumers (today):**
  - [`eval/memeval/dreaming/worker.py`](../../eval/memeval/dreaming/worker.py)
    — v1 detection-only; the gate applies the moment a `write` call
    is added to this file's Dream code path.
  - [`eval/memeval/dreaming/cli.py`](../../eval/memeval/dreaming/cli.py)
    `_handle_dream` — the v2 mutation PR will either thread a lock
    acquisition (Option A) or a `cas_version` argument (Option B)
    through this call site; the gate names which.
  - [`docs/adrs/README.md`](README.md) — the decision index row for
    this ADR is the contract-row reviewers grep for during v2 PR
    review.

- **Policy — nothing changes today.** The v1 detection-only worker
  ships unchanged. The gate is contract, not code.

- **Policy — what changes at v2.** The v2 mutation PR's checklist
  gains one hard item: *"Cites successor ADR resolving
  ADR-dreaming-020. Amendments named in that ADR are implemented in
  this PR or in a precursor PR cited in the description."*

## Open items

The successor ADR must answer **all four**. An unanswered question
means the decision wasn't made — same rule as an empty Tradeoffs &
risks section.

1. **Mutation primitive for retirement.** Choose one and pin it:
   (a) `relevancy=0.0` on the loser as the retirement sentinel
   (no protocol change; collides with legitimate low-relevancy
   writes from Daydream and forces `recall` ranking to agree on
   the threshold);
   (b) a new `tombstone: bool` field on `MemoryItem` (a
   `[CONTRACT]` change to `schema.py`, but unambiguous);
   (c) a new `MemoryStore.delete(item_id, *, cas_version=...)`
   method (a `[CONTRACT]` change to `protocols.py`, the cleanest
   but the most expensive); or
   (d) **no retirement** — Dream emits "merge-suggestion" events
   only and a separate sweep (or a human) applies them. Each
   option has a different consumer surface; the successor ADR
   picks one and names the migration.

2. **Concurrency primitive.** Option A (basedir `flock`), Option B
   (Router/Store CAS), or a justified fourth. Option C is killed
   and may not be re-proposed without new evidence overturning
   *both* review findings (concurrent-acquirer non-atomicity and
   weaker trust model than `flock`). Whichever is chosen, the named
   amendments for that option are non-optional.

3. **NFS / multi-machine support: yes or no.** Pick one explicitly:
   (a) **No** (matches today's ADR-014 and ADR-storage-001
   posture). Then Option A is safe with a hard-fail-on-NFS check;
   Option B is safe at single-machine SQLite WAL only.
   (b) **Yes**. Then both A and B fail; a distributed primitive
   (Redis lock, etcd lease, a coordinating daemon) or a single-
   writer architectural commitment is required, and the eval
   pipeline gains a deployment constraint. **Default: (a) No.**
   The successor ADR may flip this only with eval/storage sign-off.

4. **Cross-domain coordination ask.** Name exactly which of the
   following sign-offs are required given the chosen concurrency
   option (#2). Each must be a citable name + a yes; an unanswered
   sub-ask counts as the question unanswered.
   (a) **Option A path:** Ken signs off that
   `dream.lock_contended` events are acceptable measurement loss
   at planned eval concurrency (or v2 ships with retry semantics).
   Brent acknowledges the single-mutation-surface invariant before
   adding any new Router caller. Keith acknowledges the same
   invariant before letting any harness hook fire Dream (today,
   only Daydream is hook-fired per ADR-001/ADR-002 — the moment
   Dream is hook-fired, this invariant becomes load-bearing for
   harness too). Plus: the **Shape-2 decision** — does Daydream
   also wait on the basedir lock during a Dream sweep, or is
   Shape 2 explicitly accepted as out-of-scope?
   (b) **Option B path:** Brent commits to `cas_version` on
   `read` + `write` across all backends via a `[CONTRACT]` PR,
   *and* audits every existing mutation caller (including the
   Daydream Stop-hook write path) as CAS-aware in the same PR.
   Ken signs off on replay determinism for versioned items.
   Keith acknowledges recall-ranking stability invariants under
   mid-pass version churn. Plus: the **dedup-vs-CAS pick** — does
   CAS turn Router `dedup=True` for the Dream path (overriding the
   `FALSE MERGES` warning at `router.py:471-481`) or replace dedup
   as the conflict-resolution primitive?
