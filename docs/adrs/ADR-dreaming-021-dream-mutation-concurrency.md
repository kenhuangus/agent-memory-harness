---
id: ADR-dreaming-021
domain: dreaming
title: v2 Dream mutation uses `Router.delete()` under a basedir `flock` (no NFS, single-mutation-surface) — supersedes ADR-020 gate
status: Accepted
date: 2026-06-22
contract: true
supersedes: ADR-dreaming-020
superseded_by: none
owner: Scott B. (P4)
origin: docs/adrs/ADR-dreaming-020-cross-process-dream-mutation-gate.md
---

# ADR-dreaming-021: v2 Dream mutation uses `Router.delete()` under a basedir `flock` (no NFS, single-mutation-surface) — supersedes ADR-020 gate

**Status:** Accepted · **Date:** 2026-06-22 · **Contract:** yes
**Supersedes:** [`ADR-dreaming-020`](ADR-dreaming-020-cross-process-dream-mutation-gate.md) · **Superseded by:** none

> **What this ADR does.** ADR-020 declared the v2 Dream mutation PR
> *blocked* until a successor ADR resolved cross-process concurrency on
> `$MEMORY_STORE`. This is that successor. It picks one answer to each
> of ADR-020's four Open items, honors the named Option-A amendments
> (or documents why each is moot), and addresses the halliday
> SOFTENED findings from the v1 detection-only worker review. From
> the moment this ADR is Accepted, the v2 Dream mutation PR is
> unblocked — it must cite this ADR by id, implement the lock + the
> delete-call shape pinned here, and obey the named invariants.

## Context

ADR-020 closed with this gate (verbatim):

> *"No PR may flip the Dream worker from detection-only to detection+
> mutation without citing this ADR as a precondition and naming the
> successor ADR (ADR-dreaming-NNN) that resolves it."*

It required the successor to answer four Open items:

1. The **mutation primitive for retirement** — `relevancy=0.0` sentinel,
   new `tombstone` field, new `MemoryStore.delete()` method, or
   no-retirement-at-all.
2. The **concurrency primitive** — Option A (basedir `flock`), Option B
   (Router/Store CAS), or a justified fourth. Option C (lease file)
   was killed.
3. **NFS / multi-machine: yes or no.** Default (a) No.
4. **Cross-domain sign-offs** appropriate to whichever concurrency
   option is chosen.

Three facts have changed since ADR-020 was written, all of which
narrow the decision space:

1. **PR #93 landed `Router.delete()`.** The mutation primitive
   question is no longer open in the abstract — there is a shipped,
   duck-typed, idempotent, hard-delete fan-out across all three
   backends (sqlite / markdown / graph), with no CAS. Its docstring
   pins the semantics:

   > *"Delete `item_id` from EVERY registered backend; return how
   > many removed it. Write-routing is policy-driven, but delete is
   > **unconditional and complete**: under `base_all` an item lives
   > in several backends, so a correct delete clears all of them.
   > Idempotent — a backend that doesn't have the id is a no-op.
   > Duck-typed: a backend without a `delete` method (e.g. the
   > reference `InMemoryStore`) is skipped, not an error. (Adding
   > `delete` to the frozen `MemoryStore` protocol is the follow-up
   > `[CONTRACT]` change.)"*
   > — [`eval/memeval/router.py:1076-1094`](../../eval/memeval/router.py)

   That settles ADR-020 Open Item #1 by execution: retirement is a
   hard delete via `Router.delete(item_id) -> int`. The other three
   options (relevancy sentinel, tombstone field, no-retirement)
   remain available in principle but are now strictly worse — each
   would require either a frozen-contract change or a recall-side
   ranking commitment that `Router.delete()` does not need.

2. **The benchmark does not require an inter-task `dream --all`
   invocation from dreaming's lane.** Keith's full bench invocation
   process (PR #94 + the courtesy PR from our side) drives sessions
   through Daydream during the bench's natural execution shape; the
   benchmark does not call `daydream-cli dream --all` between tasks.
   Dream is invoked by the eval pipeline only at the points the eval
   author chooses, or by a human on their workstation. This bears on
   ADR-020 Open Item #4: the original concurrency story imagined
   "the eval pipeline runs dream-cli between batches AND a human
   runs it on their workstation simultaneously" as the load-bearing
   shape. Today, only the human-on-workstation half is in scope.
   The cross-machine fanout we worried about does not exist.

3. **Three halliday-SOFTENED findings from the v1 detection-only
   review now apply directly to v2.** They were softened because v1
   had no writes; they re-surface as live with v2. The parallel-
   surveys for this ADR characterized each one explicitly:

   - **Backend concurrency on `delete`.** All three backends'
     `delete` implementations are *non-atomic against a concurrent
     racing writer*: `SqliteVectorStore.delete()` is a single
     `DELETE` + `commit()` (WAL-safe but a concurrent write
     arriving after the commit can resurrect the row);
     `MarkdownStore.delete()` is `okf.delete` then `_deindex`,
     which is two operations with a filesystem unlink between
     them; `GraphStore.delete()` writes the durable row first then
     mutates RAM indexes, leaving a window where a concurrent
     write can attach edges to a node that is gone from `_nodes`
     but still present in `_in`. None of these backends has CAS;
     `Router.delete()`'s fan-out has no transactional boundary.
   - **No CAS on delete = lost-update risk for Job 2 (contradiction
     resolution).** When the future Job 2 Dream worker deletes a
     superseded version, a concurrent Daydream write can land a
     new version of the same `item_id` before the delete arrives.
     With no `cas_version` on `Router.delete(item_id)`, the new
     version is silently discarded.
   - **Recall-path post-delete staleness.** The `MarkdownStore`
     keyword-index `_deindex` runs after the unlink, so a concurrent
     `search` can hit stale postings for a brief window. SQLite's
     vector index is rebuilt eagerly. Graph's reverse-edges are
     deliberately kept on delete (`graph_store.py:321`).

   All three are real under v2 unless the concurrency primitive
   serializes Daydream and Dream against each other on the same
   basedir.

The race shapes named by ADR-020 still hold:

- **Shape 1 (Dream-vs-Dream)** — two `daydream-cli dream --all`
  processes against the same `$MEMORY_STORE` pick overlapping
  retire sets and both call `Router.delete()`. Idempotency saves
  us from double-error, but they may also both call `Router.write()`
  on consolidation winners with non-deterministic clustering,
  producing double-attribution.
- **Shape 2 (Daydream-vs-Dream)** — a Stop-hook-fired Daydream
  writes a new version of an item while a Dream sweep is computing
  its retire set, leaving Daydream's new content attached to (or
  competing with) an item Dream is about to delete.

The Daydream timing survey for this ADR confirms Shape 2 is **live**:
Daydream writes happen synchronously inside Daydream's per-session
flock (`engine.py:164`) and complete before the sidecar cursor is
written (`engine.py:184`), but that lock is `<basedir>/dream/<session_id>.lock`
— **per session, not basedir-wide**. A Dream sweep snapshotting
`store.all()` at the moment a Daydream pass for some other session
is mid-write sees a stale snapshot.

## Decisions

### Decision 1 — Mutation primitive: `Router.delete(item_id) -> int` (ADR-020 Open Item #1)

The v2 Dream worker retires items by calling `Router.delete(item_id)`.
No `relevancy=0.0` sentinel. No `tombstone` field. No "merge-
suggestion events only" fallback. Hard delete.

This is the primitive PR #93 shipped. ADR-020 listed four candidates
(a) relevancy sentinel, (b) tombstone field, (c) `MemoryStore.delete()`
contract addition, (d) no-retirement; PR #93 chose a fifth shape
the gate didn't anticipate but explicitly allowed for: a
**Router-level duck-typed delete that bypasses the frozen
`MemoryStore` protocol** until a `[CONTRACT]` PR is run. That
choice is strictly better than (a)-(c) at the Dream worker call
site:

- vs. **(a) `relevancy=0.0`**: no recall-side ranking-threshold
  commitment; no collision with Daydream legitimately writing
  low-relevancy items.
- vs. **(b) `tombstone` field**: no `[CONTRACT]` PR against the
  frozen `schema.py` required to ship Dream.
- vs. **(c) `MemoryStore.delete()`**: same delete semantics, but
  shippable without a `[CONTRACT]` PR; the protocol addition is
  named as the follow-up.
- vs. **(d) no-retirement**: actually deletes; doesn't depend on a
  separate human-or-sweep applier to close the loop.

### Decision 2 — Concurrency primitive: basedir-scope `flock` (Option A; ADR-020 Open Item #2)

The Dream worker acquires an exclusive, non-blocking advisory
`flock` on `<basedir>/.dream.lock` before any `store.all()` /
`Router.delete()` / `Router.write()` call. On contention, the
worker emits `dream.lock_contended` and exits 0 (silent idempotent
skip) — the same fail-open shape ADR-014 pins for per-session
Daydream contention.

The primitive is the one ADR-014 already established. The
implementation is a **lift of `_per_session_lock` from session
scope to basedir scope**, summarized:

```python
@contextmanager
def _basedir_dream_lock(basedir: Path) -> Iterator[None]:
    """Exclusive non-blocking flock on <basedir>/.dream.lock — ADR-021."""
    target = basedir / ".dream.lock"
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            emit("dream.lock_contended", basedir=str(basedir))
            raise _DreamLockHeld(str(exc)) from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
```

This is intentionally a structural copy of the ADR-014 primitive,
shape for shape (LOCK_EX | LOCK_NB, BlockingIOError → emit + raise,
LOCK_UN + close in nested `finally`s). The only differences are:
the lock path is basedir-wide rather than session-scoped, the
event name is `dream.lock_contended` (the same name ADR-020
called out as the measurement-loss surface), and the exception
class is distinct so the `_handle_dream` CLI catches it
separately from `_LockHeld`.

Option B (Router/Store CAS) is rejected at this time. Its
amendment bundle is heavier than Option A's: `cas_version` on
read+write across every backend via a `[CONTRACT]` PR, every
mutation caller audited as CAS-aware in the same PR, key-sharded
retire, dedup-vs-CAS pick. None of those are scoped on the
storage roadmap. Option A is purely additive within the
dreaming domain.

Option C (lease file) is killed by ADR-020 and not re-opened.

### Decision 3 — NFS / multi-machine: NO (ADR-020 Open Item #3)

`$MEMORY_STORE` on NFS/SMB is unsupported. The v2 Dream worker
hard-fails (raises) if it detects the basedir is on a network
filesystem, rather than silently degrading to "lock acquired but
not enforced." This matches the posture
[`ADR-dreaming-014`](ADR-dreaming-014-concurrent-daydream-flock.md)
already takes for the per-session lock and
[`ADR-storage-001`](ADR-storage-001-orchestrator-in-process-library.md)'s
single-machine SQLite assumption. It is also the only honest
answer given the benchmark reality: nobody runs the bench across
machines against a shared NFS basedir today.

Detection mechanism: best-effort `statvfs` / `/proc/mounts`
sniff on Linux, `getattrlist` on Darwin; on unknown platforms,
log a warning and proceed. A false-positive misdetection that
hard-fails an actually-local FS is preferable to a false-
negative that silently locks-but-doesn't-enforce on NFS.

**Bypass surface — `DREAM_ALLOW_NETWORK_FS=1`.** Power users who
need to run Dream against an actually-local FS that the heuristic
misdetects as a network FS can set the env var; the worker logs a
warning naming the detected mount and proceeds. Setting this on a
*real* NFS basedir is undefined behavior and unsupported — the
override exists to escape misdetection, not to enable a configuration
this ADR explicitly rejects.

### Decision 4 — Shape 2 (Daydream-vs-Dream): Daydream waits on the basedir lock during a Dream sweep (ADR-020 Open Item #4 / Option-A amendment (d))

Daydream invocations acquire the basedir `flock` *in addition to*
their per-session lock when a Dream sweep is in progress. Concretely:
the Daydream entry path in `engine.daydream()` wraps its work in a
non-blocking attempt on `<basedir>/.dream.lock` *before* the
per-session lock. On contention (Dream is running), Daydream emits
`daydream.dream_in_progress_skipped` and exits 0 — same fail-open
shape ADR-014 gives same-session Daydream contention. The Stop
hook fires, the user's next prompt continues, no work blocks.

**Load-bearing invariant — lock acquisition order.** The basedir
lock attempt happens **before** any per-session lock acquisition,
**before** any state read, and **before** any sidecar mutation. A
basedir-contention skip therefore holds no other lock and mutates
no state. Any future PR that reorders these acquisitions silently
breaks the no-state-mutation-on-skip property and must cite this
ADR + a successor that explicitly authorizes the new order.

This is Option-A amendment (d) from ADR-020 §Options Considered
chosen explicitly: Daydream *does* wait on the basedir lock during
a Dream sweep, rather than Shape 2 being declared "out-of-scope
with documented surface area." Rationale: the parallel survey
showed the backend `delete` operations are non-atomic against
concurrent writes (sqlite WAL doesn't block writes during delete;
markdown unlink + deindex is two steps; graph durable-then-RAM
ordering). Letting Daydream write during a Dream sweep would
require the Dream worker to either retry-on-version-skew (which
needs CAS we don't have) or accept the lost-update / resurrected-
row races the survey identified. Cheaper to serialize.

### Decision 5 — Cross-domain sign-offs (ADR-020 Open Item #4 / Option-A path)

- **Ken (eval) on `dream.lock_contended` measurement loss.**
  ADR-020 made this a hard sign-off. Today it appears **moot**:
  Keith's full bench invocation process (PR #94) does not invoke
  `daydream-cli dream --all` between tasks. Dream contention on
  the basedir lock during a bench run is structurally impossible
  on the supported eval shape — there is only one Dream caller
  (the human-on-workstation or the eval-pipeline-end-of-run
  caller, whichever the eval author chooses), and no eval
  measurement is gated on Dream invocation timing.
  **Closure status:** moot-pending-Ken-countersignature, NOT
  unilaterally closed. ADR-020 §Decision §2 required the
  successor to *honor* the named Option-A amendments; the
  closure mechanism amendment (c) names is Ken's explicit
  sign-off. This ADR documents the change in circumstance (no
  mid-bench Dream invocations) and recommends moot status, but
  the v2 Dream mutation implementation PR description must
  cite Ken's acknowledgement of the moot before it merges. If
  Ken disagrees with the moot framing, this ADR is amended to
  reinstate the sign-off as a hard ask. The `dream.lock_contended`
  event still ships for the human-runs-it-twice-in-parallel
  case; the moot status reverses to a live sign-off ask if the
  eval pipeline ever calls `dream-cli` mid-bench.
- **Brent (storage) on the single-mutation-surface invariant.**
  Acknowledged via PR #93's shipped contract: `Router.delete()`
  is duck-typed, idempotent, fan-out across all backends. Brent
  owns the `MemoryStore.delete()` `[CONTRACT]` follow-up to
  formalize the protocol shape; until then, the duck-typed
  fan-out is the invariant Dream depends on. Cross-domain ask
  on Brent: do not add a new Router-level writer (beyond
  Daydream and Dream) without revisiting this ADR.
- **Keith (harness) on the no-hook-fired-Dream invariant.**
  Today, Dream is not Stop/PreCompact-hook-fired —
  [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md)
  and [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)
  pin Daydream to hooks and Dream to a deliberate CLI invocation.
  If a future ADR makes Dream hook-fireable, the basedir lock
  contention shape changes (every Stop hook contends against
  every Dream sweep), and this ADR must be revisited. Keith's
  acknowledgement is forward-looking, not present-tense.

### Decision 6 — Option-A amendments from ADR-020 §Decision §3, accounted for

- **(a) Atomic journal for write-merge + version-bump.** Documented
  as **not needed** under this ADR's shape. ADR-020 §3(a) required
  a journal because the imagined v2 was *consolidate winners +
  write-back the merge + bump version + retire losers* as one
  semantic unit, and a crash mid-sequence would leave the store
  inconsistent. This ADR's shape is **delete-the-losers via
  `Router.delete()` only** — the consolidation winner is the
  surviving original item (`item_id` unchanged), no version bump
  on the winner, no write-back of the merge. `Router.delete()`
  is idempotent across backends and crash-resumable: a crash
  mid-fan-out leaves some backends with the item deleted and
  others not; the next Dream sweep observes the same cluster,
  calls `Router.delete()` again, and the fan-out completes
  (idempotent). The journal is replaced by re-clustering at
  next-sweep + delete idempotency. This is honest, not a dodge:
  it costs us "the winner gets a richer consolidated summary,"
  which is a Job 2 feature we explicitly defer to a successor
  ADR if and when we want it.
- **(b) Hard-fail on NFS.** Adopted — see Decision 3.
- **(c) Ken `dream.lock_contended` sign-off.** Mooted — see
  Decision 5.
- **(d) Shape-2 decision.** Adopted: Daydream waits on the basedir
  lock during a Dream sweep — see Decision 4.

## Rationale

The forward-defense ADR-020 wrote into existence has paid off
exactly as designed: by the time we needed to answer it, the
mutation primitive question had been narrowed by execution
(`Router.delete()` landed), the multi-machine question had been
narrowed by the bench shape Keith landed, and the only genuinely
open decisions were the concurrency primitive and the Shape-2
call. Both go to Option A:

- **Cost.** Zero cross-domain commitments. Lock file lives in
  dreaming's basedir, primitive is already shipped, shape is a
  copy-paste of ADR-014.
- **Honesty.** The decision matches the real deployment.
  `$MEMORY_STORE` is single-machine, the bench is single-machine,
  the user's workstation is single-machine. Pretending otherwise
  to keep a CAS-shaped escape hatch costs more than it earns.
- **Reversibility.** When (if) Brent ships `cas_version` on
  read+write and the every-writer audit happens, this ADR is
  superseded by an ADR-021-successor that swaps the lock for
  CAS. The lock comes out, the events change from
  `dream.lock_contended` → `dream.cas_skewed`. The migration
  cost is contained to dreaming's worker + the Daydream
  acquisition path.
- **Failure mode is the right one.** Lock contention is a silent
  skip + an emitted event. Not a wedged process, not a corrupt
  store, not a wrong recall. The skip surfaces in the event
  stream as observable measurement loss; the failure mode is
  *visible exactly where ADR-007's events platform looks*.

The Job 2 lost-update risk (deleting a superseded version while
Daydream writes a new version) is dispatched by Decision 4:
Daydream can't write during a Dream sweep, so there is no race
to lose. The cost is Daydream skips on Stop-hook fire while
Dream is running; the survey shows Dream sweeps are bounded in
duration and contention windows are short.

## Tradeoffs & risks

- **Daydream Stop hooks get silently skipped during a Dream sweep.**
  This is the cost of Decision 4. A Stop hook firing while
  `dream --all` is mid-pass emits
  `daydream.dream_in_progress_skipped` and returns 0, leaving
  the user's session unrecorded for that turn. The next Stop
  hook (or PreCompact) catches the same uncaptured turns via
  the existing cursor-no-advance-on-skip semantics from
  [`ADR-dreaming-013`](ADR-dreaming-013-cursor-advance-ordering.md).
  Acceptable: dream sweeps are deliberate, not hot-path.
  Mitigation: emit at warning level so the diary records the
  skip; if a user complains about missing memories, this is
  the first thing to check.
- **The journal-replaced-by-re-clustering shape (Decision 6(a))
  costs Job 2 consolidation richness.** Today's v2 is "delete
  the losers"; we do not write a consolidated summary back to
  the winner. If a future product requirement says "the winner
  should carry a merged summary of the cluster," this ADR is
  insufficient and we go back to ADR-020-style atomic-journal
  reasoning. Documented as the deferred Job-2 feature, not a
  hidden cost.
- **Backend `delete` non-atomicity is mitigated, not solved.**
  Decision 4 closes the cross-writer race (Daydream-vs-Dream).
  Decision 2 closes the cross-Dream race (Dream-vs-Dream). The
  in-backend non-atomicity (sqlite WAL delete vs. concurrent
  read, markdown unlink vs. deindex, graph durable-vs-RAM
  ordering) is now structurally inaccessible because no two
  writers reach the backends simultaneously. The basedir lock
  does NOT serialize readers, however — `recall` runs on every
  user prompt and can land mid-Dream-sweep. **Pinned failure
  mode (markdown):** a `search()` whose keyword-index posting
  is resolved after `okf.delete` (unlink) but before `_deindex`
  hits a missing file; the search-result loop **silently
  skips** that posting (no exception raised, the result list
  is one item shorter). Verified against `markdown_store.py`'s
  per-posting open-or-skip pattern. **Sqlite vector** rebuilds
  its index eagerly inside the same `commit()`; a mid-delete
  reader sees a consistent post-delete view. **Graph** see the
  framing note below. Not corruption, no resurrected rows; a
  brief search-result-missing-one-item window is the worst
  observable. Documented rather than fixed.
- **`Router.delete()` has no `cas_version`.** Lost-update risk
  for Job 2 (delete-the-superseded-version) is real *in
  principle*. Decision 4 closes it *in practice* by serializing
  Daydream against Dream. If the basedir lock is ever weakened
  (e.g. a future "Daydream is too important to skip" amendment),
  the lost-update reappears. Pinned: any amendment that lets
  Daydream proceed during a Dream sweep must accompany a
  `cas_version` follow-up.

- **Graph store framing — `_in` reverse edges are a permanent
  property, not a race window.** OTHER nodes' reverse edges
  into a deleted `item_id` are intentionally preserved
  (`graph_store.py:321` docstring) and resolve to nothing for
  the lifetime of the store; they are not a transient
  consequence of delete ordering. The basedir lock matters
  for the *actually-racy* case: preventing *new* writes from
  attaching edges to a node mid-delete (so a fresh write
  doesn't observe `_nodes` empty + `_in` populated and infer
  the node into existence). A future reader who tries to
  "fix" the permanent-by-design `_in` retention is doing
  cleanup the graph store explicitly rejects — they should
  read the docstring before patching.
- **The NFS hard-fail is a heuristic.** A misdetected non-network
  FS that hard-fails the dream worker is a user-visible bug.
  Mitigation: log the detected mount + the env var, name the
  override (`DREAM_ALLOW_NETWORK_FS=1` for power users who know
  what they're doing).
- **The Job 2 (contradiction) worker is not in scope here.**
  This ADR pins Dream's *primitive surface* (`Router.delete()`
  + basedir flock + Daydream serialization). The Job 1 (dedup
  consolidation) worker can ship against this ADR immediately;
  Job 2 (contradiction resolution) inherits the same primitives
  but may need a per-Job ADR for clustering semantics. Out of
  scope here.
- **Reversibility cost named.** Swapping Option A for Option B
  later means: removing `_basedir_dream_lock`, removing the
  Daydream basedir-lock acquisition, plumbing `cas_version`
  through `Router.delete()` and `Router.write()`, and writing
  the every-writer audit. Estimated at ~1 week of dreaming
  work plus Brent's storage-side `[CONTRACT]` PR. Not free,
  but contained — and only paid if we discover that lock
  contention is hurting bench measurement, which the moot
  status of Decision 5 says we won't.

## Consequences for the build

- **Contract — source of truth:** this ADR, plus
  `Router.delete()` at
  [`eval/memeval/router.py:1076-1094`](../../eval/memeval/router.py),
  plus the to-be-added `_basedir_dream_lock` in
  [`eval/memeval/dreaming/_state.py`](../../eval/memeval/dreaming/_state.py).

- **Shape (the v2 Dream worker contract):**
  - The Dream worker acquires `<basedir>/.dream.lock` before any
    `store.all()` / `Router.delete()` / `Router.write()` call.
  - On `_DreamLockHeld`, the worker emits `dream.lock_contended`
    and the CLI exits 0.
  - The worker retires items by calling
    `Router.delete(item_id) -> int`. It does not call
    `Router.write()` on a "consolidated winner" — the winner is
    the surviving original item.
  - The worker hard-fails (raises) if `$MEMORY_STORE` is detected
    on NFS/SMB; the CLI catches and exits 0 with a
    `dream.unsupported_fs` event. The NFS hard-fail is bypassable
    via `DREAM_ALLOW_NETWORK_FS=1`; when set, the worker logs a
    warning naming the detected mount and proceeds. Setting this
    variable on a real NFS basedir is undefined behavior and
    unsupported.

- **Shape (the Daydream acquisition contract):**
  - `engine.daydream()` acquires `<basedir>/.dream.lock` (non-
    blocking) **before** the per-session lock. On contention it
    emits `daydream.dream_in_progress_skipped` and returns
    without touching the store or the cursor (no
    `_state` mutation, no `Router.write()`, no event other than
    the skip).

- **Policy — `[CONTRACT]` PR process.** This ADR's
  implementation PR touches `Router.delete` consumers and the
  Daydream entry path; loop Brent (storage) on the
  Router-consumer changes and Keith (harness) on the
  Stop-hook timing implications.

- **Policy — Job 2 mutation primitive is bound.** Job 2
  (contradiction resolution) ships against this ADR's primitives
  (`Router.delete` + basedir flock + Daydream serialization). A
  Job 2 PR that introduces a new mutation primitive (consolidated-
  write-back, tombstone field, CAS-aware delete) requires a
  successor ADR; it cannot land under the duck-typed
  `Router.delete()` contract this ADR pins.

- **Policy — exhaustive consumers:**
  - [`eval/memeval/dreaming/worker.py`](../../eval/memeval/dreaming/worker.py)
    — gains the lock acquisition + `Router.delete()` calls
    on the retire path. Today this file is a
    `NotImplementedError` stub.
  - [`eval/memeval/dreaming/cli.py:_handle_dream`](../../eval/memeval/dreaming/cli.py)
    — gains catches for `_DreamLockHeld`
    (emit + exit 0), `_UnsupportedFsError` (emit + exit 0).
  - [`eval/memeval/dreaming/engine.py:daydream`](../../eval/memeval/dreaming/engine.py)
    — gains the basedir-lock acquisition before the per-session
    lock, with skip-on-contention.
  - [`eval/memeval/dreaming/_state.py`](../../eval/memeval/dreaming/_state.py)
    — gains `_basedir_dream_lock` (the ADR-014 primitive lifted
    to basedir scope) and `_DreamLockHeld`.
  - [`eval/memeval/router.py`](../../eval/memeval/router.py)
    — `Router.delete()` consumer (no router changes; consumer
    only). The future `MemoryStore.delete()` `[CONTRACT]` PR
    that promotes the duck-typed shape into the frozen
    protocol does not block this ADR.
  - [`docs/adrs/README.md`](README.md) — two updates: (a) a
    **new** index row added for ADR-021 (Accepted, contract:
    yes); (b) the existing ADR-020 row's Status flipped to
    "Superseded by [ADR-dreaming-021](...)". Closure is not
    complete until both rows reflect it.
  - [`docs/adrs/ADR-dreaming-020-cross-process-dream-mutation-gate.md`](ADR-dreaming-020-cross-process-dream-mutation-gate.md)
    — frontmatter mutation: `status: Superseded` (was
    `Accepted`) and `superseded_by: ADR-dreaming-021` (was
    `none`). Per CLAUDE.md, these are the explicit allowed
    mutations on a superseded ADR; the implementation PR must
    not miss them.

- **Policy — Daydream-vs-Dream serialization is mandatory.**
  Any future PR that makes Daydream proceed during a Dream
  sweep must cite this ADR and a successor that explicitly
  authorizes it (probably accompanied by a `cas_version`
  primitive). The default is serialize-via-lock.

- **Policy — the `MemoryStore.delete()` `[CONTRACT]` PR is the
  follow-up.** PR #93's docstring already names it. This ADR
  ships against the duck-typed `Router.delete()`; the
  protocol promotion is a separate Brent-owned PR, not a
  precondition.

## Open items

- **The `MemoryStore.delete()` `[CONTRACT]` PR.** Tracked, not
  scoped here. Owner: Brent (storage). When it lands, the
  `Router.delete()` duck-typing layer becomes redundant; this
  ADR's policy survives the swap unchanged.
- **Job 2 (contradiction resolution) worker shape.** **CLOSED 2026-06-23**
  (per execution; closed by the Job 2 PR landing
  `_detect_contradictions` in `eval/memeval/dreaming/worker.py` against
  `JOB2_CONTRADICTION_RUBRIC.md`). Job 2 ships against `self.store.delete`
  (the frozen `MemoryStore` protocol promoted by PR #99) alone — no
  consolidated-write-back, no new mutation primitive. The forecast
  ("`Router.delete()` is sufficient") was confirmed by execution: the same
  delete-the-loser shape works for "delete the LLM-judged
  superseded-version" exactly as it works for "delete the duplicate."
  The LLM judges only whether a pair contradicts; the worker picks the
  loser deterministically by Job 1 §D5a/D5b recency rule. No successor ADR
  required.
- **NFS detection heuristic robustness.** First production
  miss-detection is the first amendment. Tracked as an
  implementation-PR follow-up, not an ADR-level open item.
- **Recall-path mid-delete staleness as a recall ADR.** Not
  in dreaming's lane to resolve — recall ranking under
  partial deletes is a Brent/Keith conversation if it ever
  matters for a benchmark.

