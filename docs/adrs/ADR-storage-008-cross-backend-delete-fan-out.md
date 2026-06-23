---
id: ADR-storage-008
domain: storage
title: Cross-backend delete is unconditional and complete — Router.delete fans out to every backend (count); RouterStore.delete returns bool
status: Accepted
date: 2026-06-23
contract: false
supersedes: none
superseded_by: none
owner: Brent (P3)
origin: DECISION_LOG D036/D038 (capstone-workspace); PR #93, PR #99/#101
---

# ADR-storage-008: Cross-backend delete is unconditional and complete

**Status:** Accepted · **Date:** 2026-06-23 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context

`delete(item_id) -> bool` was promoted into the frozen `MemoryStore` protocol via a four-owner `[CONTRACT]` sign-off (D038, PR #99/#101) — the **signature** is now in [`architecture.md`](../../architecture.md):106 and [`protocols.py`](../../eval/memeval/protocols.py):66, so the bare signature is **not drift**. What the contract line *cannot convey*, and what every consumer of the retention/version primitive depends on, is the **cross-backend behavior**: how delete composes across a `base_all` write fan-out.

The machinery shipped first solo-additive/duck-typed (D036, PR #93) on the three durable backends + Router/RouterStore, then was promoted to the protocol (D038). delete is the primitive that **version-highest-wins**, **retention/TTL** (ADR-P9), and the **v2 Dream mutation** ([`ADR-dreaming-021`](ADR-dreaming-021-dream-mutation-concurrency.md), `Router.delete` under a basedir flock) all build on — so its exact semantics are load-bearing, not incidental.

The key asymmetry: **writes are policy-driven** ([`ADR-storage-004`](ADR-storage-004-router-owns-write-path-routerstore-seam.md): `base_all`/`base_selective`/`single` decide *where* an item lands) but **delete must be unconditional** — under `base_all` an item lives in several backends, so a correct delete must clear *all* of them regardless of which write policy placed it.

## Options considered

- **Policy-mirrored delete** (delete only from the backends `write_policy` would have written). Rejected: the active policy at delete-time may differ from the policy at write-time, and `base_all` items live everywhere — a policy-scoped delete would leave orphans in untouched backends, silently resurrecting on the next read.
- **Unconditional fan-out delete (chosen):** `Router.delete` removes the id from **every** registered backend, idempotently (a backend without the id is a no-op).
- **Return type — bool everywhere.** Rejected for the Router: the Router is not a `MemoryStore` (it has no protocol `delete` return to honor), and a per-backend **count** is useful (it tells how many backends actually held the id). The `MemoryStore` facade owes a bool.

## Decision

**Delete fans out unconditionally and completely.** `Router.delete(item_id)` ([`router.py`](../../eval/memeval/router.py):1090) removes the id from **every registered backend** (idempotent, duck-typed-safe, de-duplicating backends registered under multiple names) and returns the **per-backend count**. `RouterStore.delete` returns **bool** (`Router.delete(item_id) > 0`) to conform to the `MemoryStore` protocol — mirroring the write asymmetry (`Router.write -> WriteReceipt` vs `RouterStore.write -> None`). Per-backend delete is **durable-first atomic** (e.g. graph `_persist_delete` rollback; sqlite `DELETE FROM` rollback) and, for the OKF/markdown bundle, **unlinks every doc that parses to the id** (canonical + foreign-imported bundles) so a reload cannot resurrect it.

## Rationale

Delete must be the inverse of `base_all`: because the recall-safe default scatters one memory across three (now four) backends, the only correct delete is the one that clears all of them — anything policy-scoped leaves a resurrectable orphan. The Router returning a *count* and the facade returning a *bool* is the same principled split as write: the Router is the routing engine (richer return), the `RouterStore` is the protocol-shaped store (the contract's bool). The foreign-bundle rescan (unlink every doc that *parses* to the id, not just the canonical filename) is what makes the delete durable across an imported bundle — a gap the cross-vendor gate caught (D036 R1: foreign-bundle resurrection).

## Tradeoffs & risks

- **Fan-out cost on delete.** Every delete touches every backend, including ones that don't hold the id (a no-op probe each). Accepted: correctness over a micro-cost; idempotency makes the extra probes safe.
- **OKF foreign-bundle rescan can be O(N).** The audit ([`ADR-storage-009`](ADR-storage-009-backend-durability-audit-hardening-arc.md)) flags `delete()` full-bundle rescan as a HIGH perf issue under dedup bursts — the fast-path fix (unlink at the deterministic `_doc_relpath` first, rescan only for foreign filenames) is queued in the hardening arc. The *correctness* of unlink-every-parsing-doc stands; the *cost* is a known hardening target.
- **Count vs bool is a maintenance asymmetry.** Callers must know `Router.delete -> int` and `RouterStore.delete -> bool`. Documented inline; locked by the delete eval (D036: RouterStore fan-out + count).
- **The behavior, not the signature, is undocumented.** architecture.md:106 has the signature but no prose on unconditional fan-out / count-vs-bool. This ADR is that prose; a one-line arch caveat is owed.

## Consequences for the build

- **Policy:** delete is **unconditional and complete** — it clears every backend regardless of `write_policy`. Writes are policy-driven; deletes are not. Idempotent on a missing id.
- **Contract-adjacent:** `delete(item_id) -> bool` is now on the frozen `MemoryStore` protocol (the `[CONTRACT]` change is D038, owned by all four; this ADR is the storage-side *why* of its cross-backend behavior, not the contract edit itself). `Router.delete -> int` is the non-protocol Router method.
- **Affected files:** [`eval/memeval/router.py`](../../eval/memeval/router.py) (`Router.delete` :1090, `RouterStore.delete`); per-backend `delete` in [`stores/`](../../eval/memeval/stores/) + [`okf.py`](../../eval/memeval/okf.py); [`protocols.py`](../../eval/memeval/protocols.py):66 (the promoted signature).
- **Cross-links:** delete is the primitive for version-highest-wins and retention/TTL (ADR-P9), and the v2 Dream mutation ([`ADR-dreaming-021`](ADR-dreaming-021-dream-mutation-concurrency.md)); the write fan-out it inverts is [`ADR-storage-004`](ADR-storage-004-router-owns-write-path-routerstore-seam.md).
- **Note on a merge slip (D038 postscript):** PR #99 merged the pre-fold contract commit (a read-only gate detached HEAD in the shared workspace); PR #101 landed the byte-identical completeness folds. main stayed green throughout (nothing isinstance-checks the test fakes). Recorded here only so the history is legible; the lesson (verify `git branch -vv` tracks your amends before pushing) is in agent memory.
- **Doc-reconciliation owed:** add a one-line architecture.md caveat that delete fan-out is unconditional while writes are policy-driven (count on Router, bool on the store facade).
