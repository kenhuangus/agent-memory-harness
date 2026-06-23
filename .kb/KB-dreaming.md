# Knowledge Base — dreaming

**Domain owner:** Scott
**First entry:** 2026-06-22

Append-only journal of project-story snapshots for the **dreaming** workstream.
See [README.md](README.md) for conventions.

---

## 2026-06-22T11:32 — entry 1

**Triggered by:** Initial KB seeding via cross-cutting `/kb all` run — establishes baseline state of the dreaming workstream as the `.kb/` convention lands in the repo.
**Branch:** harness/add-kb-command
**Related ADRs:** ADR-dreaming-001 through ADR-dreaming-019
**Cross-domain run:** [KB-harness.md](KB-harness.md), [KB-storage.md](KB-storage.md), [KB-eval.md](KB-eval.md)

### Summary
The dreaming workstream owns the two isolated subconscious functions — **Daydreaming** (in-session capture, Stop/PreCompact-fired, day scope) and **Dreaming** (whole-store consolidation, CLI-driven, night scope) — plus the shared subconscious model and the redaction trust boundary that keeps secrets out of model calls. v1 has the full functional loop closed: PR5 (#48) shipped the `daydream-cli` console script and the Stop-hook plugin shim, which together turn a Claude Code session-end into a Daydream invocation that reads the transcript, redacts, calls the LLM, and writes memories. Behind that one PR sits 19 ADRs covering the trigger model, CLI shape, LLM client, events stream, redaction scope, state management, and the operational contracts with the harness hooks.

### Key state
Daydreaming = in-session capture, auto-fired by the Stop hook (ADR-dreaming-001); Dreaming = whole-store consolidation via `memory dream --all` — its own entrypoint (ADR-dreaming-002). The CLI was renamed `memory` → `daydream-cli` (ADR-dreaming-016) to eliminate PATH collisions, with argparse exit-code policy set to 1 not 2 (ADR-dreaming-018) because Claude Code reserves exit 2 for hook-blocking. The subconscious model is a swappable `LLMClient` returning a `Completion` dataclass with token counts (ADR-dreaming-006, supersedes 003); default = `inclusionai/ling-2.6-flash` via OpenRouter (ADR-dreaming-004); missing API key is fail-open with a `llm_unavailable` event and no cursor advance (ADR-dreaming-012). Redaction is structurally enforced via the `RedactedText` NewType (ADR-dreaming-010), mypy-checked at the seam, with expanded DB/URL-credential scope and an FP/FN audit file (ADR-dreaming-011 amends 005). Cursor advance is memories-then-cursor with atomic sidecar write and no advance on exception (ADR-dreaming-013). Concurrent Daydream invocations are serialized per `session_id` via `flock` with idempotent exit-0 (ADR-dreaming-014). `$MEMORY_STORE` is a directory, not a file-sentinel (ADR-dreaming-019, supersedes 015 §1).

### Open items
- The events shim is still a no-op + local `daydream-events.jsonl` diary per ADR-dreaming-009; it stays that way until the harness-bound observability stream (ADR-harness-007, Langfuse) ships. This is an explicit hand-off back to the harness workstream.
- The PreCompact hook concurrency contract (ADR-dreaming-017) is implemented as silent-skip when Stop is in-flight, but the cross-hook race contract is convention-enforced — no automated test confirms the two hooks don't collide under real Claude Code load.
- v1 redaction scope is "DB/URL credentials + custom plugins" per ADR-005/011; out-of-scope items (PII, prompt-injection content) are explicitly deferred and tracked in the FP/FN audit file.

### Artifacts at time of entry
- [`architecture.md`](../architecture.md)
- [`prd.md`](../prd.md)
- [`plan.md`](../plan.md)
- [`.env.example`](../.env.example) — daydreaming env-var surface
- `eval/memeval/dreaming/` — engine, CLI, redaction, events, llm, state, tests
- [`docs/adrs/`](../docs/adrs/) — ADR-dreaming-001 through ADR-dreaming-019

---

## 2026-06-22T22:30 — entry 2

**Triggered by:** PR #77 opened — Stop-hook migration that closes the PR5-to-bench gap by wiring `hooks_handler.handle()` to fire `daydream-cli daydream`.
**Branch:** `dreaming/migrate-stop-hook-to-daydream`
**Related ADRs:** ADR-dreaming-001 (Stop-fired Daydreaming — implementation finally lands), ADR-dreaming-017 (PreCompact concurrency + transcript trust — consumed by handler timeout policy), ADR-dreaming-018 (CLI exit-code 1 — informs subprocess fail-open contract), ADR-harness-006 (fail-open). New cross-domain: ADR-harness-011 (plugin as dumb client of the router — Keith's #76 resolves audit blocker #4 in our favor).

### Summary
PR #77 is the last v1 piece dreaming owed: with PR5 (#48) having shipped `daydream-cli daydream` as a working standalone command, this PR finally wires Claude Code's actual Stop/PreCompact hooks to call it. The canonical plugin's `hooks_handler.handle()` shells out via `subprocess.run(["daydream-cli", "daydream"], …)` on gated events, with a selective env-passthrough allowlist (drops `ANTHROPIC_API_KEY`/AWS-style secrets), per-event timeout (600s Stop async / 120s PreCompact sync), and fail-open absorption of every exception class. `daydream-cli daydream` itself gained a three-surface OPENROUTER_API_KEY-unset alert — stderr line, WARNING log, and a `daydream.openrouter_unset` diary event — the diary event exists because in CC's async-Stop subprocess path stderr is captured and discarded, so without an event-stream signal an unset key would be invisible. Legacy `eval/memeval/claudecode/plugin/` got a DEPRECATED banner (no deletion until a green migrated-bench run). The PR was workflow-disciplined: jasnah rubric of 79 boolean criteria, halliday adversarial pre-impl review with 11 findings, scope reset by the user pulling cross-domain creep (Router.write swap) out of our lane, bounded fix-loop, then implementation with end-to-end smoke proving handler → subprocess → daydream-cli → engine → diary fires cleanly.

### Key state
After PR #77 merges, the dreaming-domain side of the v1 loop is complete: Stop fires daydream, OPENROUTER status is observable everywhere, the legacy plugin tree is signposted for removal. **The bench-readiness picture shifted dramatically during this arc** because the team shipped in parallel: Ken's #74 chose a non-fatal OPENROUTER advisory instead of the hard env-gate the audit wanted (architectural reframe — bench runs on seeded memory; daydream is the *lift* on top), and Keith's #76 (ADR-harness-011) collapsed `_Engine` to a dumb client of a `RouterStore` from `cookbook_memory.core.contract.build_store()` — resolving the audit's blocker #4 via a cross-lane move (harness, not storage). Two of the original five audit blockers resolved themselves while we were drafting; one (Ken's `_solve_plugin_real` topology) remains the single open dependency for an end-to-end memoryagentbench run. Our own `cli.py:_make_store` is still MarkdownStore-direct — findable via Keith's RouterStore fusion-mode RRF on the recall side, so not blocking, but a symmetry follow-up if we want daydream writes to route through `Router.write` for cleanliness.

### Open items
- **`_solve_plugin_real` topology** (Ken's lane, eval) — the only remaining blocker for an end-to-end bench. Today's single-turn + `memory-cli remember` back-door means Stop never fires during a real bench task; needs one Claude turn per `task.sessions[i]`.
- **`cli.py:_make_store` Router-alignment** — opt-in follow-up to mirror Keith's `build_store()` pattern. Cross-package import edge (eval/memeval/dreaming → plugin/cookbook_memory) is the friction. Not blocking the bench.
- **`daydream.precompact_skipped_stop_running` event** (ADR-017 open item, engine-only) — observability for the PreCompact-when-Stop-mid-flight silent skip. Deferred.
- **Transcript-path hardening** (ADR-017 carve-out) — accepted v1 risk; defer until plugin moves beyond personal-machine eval.
- **Night-dream worker body** — `worker.py` still raises `NotImplementedError`. `daydream-cli dream --all` fail-opens to `daydream.dream_all_skipped`. Separate ADR + PR when prioritized.
- **Legacy `eval/memeval/claudecode/plugin/` deletion** — banner only in PR #77; prune after the first green migrated-bench run.

### Artifacts at time of entry
- [`eval/memeval/dreaming/cli.py`](../eval/memeval/dreaming/cli.py) — `daydream-cli` with new OPENROUTER alert
- [`plugin/cookbook_memory/adapters/claude_code/hooks_handler.py`](../plugin/cookbook_memory/adapters/claude_code/hooks_handler.py) — handler now fires daydream on Stop/PreCompact
- [`eval/memeval/dreaming/tests/MIGRATION_STOP_HOOK_RUBRIC.md`](../eval/memeval/dreaming/tests/MIGRATION_STOP_HOOK_RUBRIC.md) — 79-criterion completion rubric for PR #77
- [`plugin/tests/test_hooks_handler_subprocess.py`](../plugin/tests/test_hooks_handler_subprocess.py) — new integration tests for the subprocess wiring
- [`docs/adrs/ADR-harness-011-plugin-as-dumb-client.md`](../docs/adrs/ADR-harness-011-plugin-as-dumb-client.md) — Keith's #76 (cross-domain context this entry depends on)
- [`eval/memeval/claudecode/plugin/README.md`](../eval/memeval/claudecode/plugin/README.md) — DEPRECATED banner
- `/tmp/team-coordination-bench-readiness.md` — cross-domain coordination writeup (not committed; regenerable from this entry + main)

### Notable since last entry
- **PR #77 opened** — handler wiring + OPENROUTER alert + deprecation banner; 79-criterion rubric; end-to-end smoke green.
- **Ken's #74 landed** — OPENROUTER policy is *advisory*, not gated. Reframes the bench as a two-run comparison (seeded baseline vs. daydream-lifted), invalidating the audit's "key required for meaningful run" premise.
- **Keith's #76 + ADR-harness-011 landed** — plugin is now a dumb client of `RouterStore`. `_Engine.remember` and `_Engine.recall` both route through Router with auto-selected profile (VOYAGE_API_KEY → accuracy; else fusion; never speed by default). The audit's blocker #4 dissolves.
- **Audit's blocker set went 5 → 1 in 12 hours** without us doing the cross-domain work — two by team parallel work, one (handler wiring) by us, one (deprecation) by us. Lesson re-learned: re-pull main + re-check blocker status before assuming anything cross-domain is still open.
- **Respectful-critic discipline tested twice this arc:** once on Router.write scope creep (user explicitly pulled me back into our lane), once on the file-sentinel MEMORY_STORE design (caught during PR5 review, not by me proactively). Both now memory-encoded.

---

## 2026-06-22T19:14 — entry 3

**Triggered by:** PR #88 merged — first substantive `DreamingWorker.run()` body shipped (Job 1 dedup detection-only) bundled with ADR-dreaming-020 as a forward-defense gate on the v2 mutation half.
**Branch:** `dreaming/dream-worker-job1-detection`
**Related ADRs:** ADR-dreaming-002 (consolidation CLI — first of its four jobs implemented), ADR-dreaming-020 (new; the forward-defense gate)

### Summary
The night-Dream worker was a `NotImplementedError` stub since PR4. PR #88 fills in the **first substantive case**: Job 1 of ADR-002, detection-only — walk `store.all()`, group items by a stdlib-normalized content key, return a JSON summary dict, no mutation. The critical finding driving detection-only was that the public `MemoryStore` protocol had no `delete` method at the time, so cross-session near-duplicates with different `item_id`s couldn't be retired inside the contract. Rather than invent a fake delete (e.g. `relevancy=0` as a soft-delete tombstone — poisons the recall-time relevancy signal), the PR ships detection and pairs it with **ADR-dreaming-020**, a forward-defense gate ADR: no v2 mutation PR may ship without a successor ADR resolving four open items (mutation primitive, concurrency primitive, NFS support yes/no, cross-domain sign-offs). The gate is the contract — a decision-rule entry in the index reviewers grep for during any subsequent mutation PR.

### Key state
The worker returns `{schema, version, mode: "detection", jobs_run: [...], skipped_jobs: [...], counts, clusters}`. `daydream-cli dream --all` no longer fail-opens on the happy path. ADR-harness-006 fail-open still holds at the CLI on unexpected exceptions. Canonical workflow established for this domain: jasnah rubric (12 sections, 50+ boolean criteria) → halliday adversarial review (returned FAIL with 5 blockers; 4 applied + 1 declined as redundant) → impl → jasnah final grade PASS. The §J3 broadened AST check was the one rubric self-flag carried forward: vacuous on `store.<attr>` because the impl uses `self.store.<attr>` — pinned for the v2 mutation rubric to fix.

### Open items
- The v2 Dream mutation half — gated by ADR-020 until a successor ADR picks the mutation primitive and concurrency model.
- INITIAL_DREAM_RUBRIC.md §J3 AST check is vacuous; v2 rubric should broaden to match `self.store.*`.
- ADR-015 sidecar-vs-store TTL scope question still open for Job 4.
- Jobs 2 (contradiction) and 3 (governance) untouched.
- PR #83 (Ken's `_solve_plugin_real` topology, our courtesy fix) back-burnered — user signaled benchmark switch is coming.

### Artifacts at time of entry
- [`eval/memeval/dreaming/worker.py`](../eval/memeval/dreaming/worker.py) — detection body
- [`eval/memeval/dreaming/tests/INITIAL_DREAM_RUBRIC.md`](../eval/memeval/dreaming/tests/INITIAL_DREAM_RUBRIC.md) — the rubric
- [`eval/memeval/dreaming/tests/test_worker.py`](../eval/memeval/dreaming/tests/test_worker.py) — 44 unit tests
- [`docs/adrs/ADR-dreaming-020-cross-process-dream-mutation-gate.md`](../docs/adrs/ADR-dreaming-020-cross-process-dream-mutation-gate.md) — forward-defense gate

### Notable since last entry
- PR #77 (entry 2) merged the Stop-hook wiring. The bench-readiness story stayed at "Ken's `_solve_plugin_real` topology" as the single remaining blocker — then the user signaled the benchmark itself is changing (switching from memoryagentbench to `agents-never-forget` / SWE-Bench-CL). This made PR #83 back-burner.
- CodeRabbit caught 3 inline findings on PR #88 (rubric pushback stale wording; Ruff E702; `is not None` tighter than the truthy-rejection contract halliday pinned). All addressed pre-merge plus docstring-coverage backfill.

---

## 2026-06-22T21:33 — entry 4

**Triggered by:** PR #96 merged — ADR-dreaming-021 successor to ADR-020. Picks the v2 Dream mutation primitive + concurrency model.
**Branch:** `dreaming/adr-021-dream-mutation-concurrency`
**Related ADRs:** ADR-dreaming-021 (new), ADR-dreaming-020 (superseded), ADR-dreaming-014 (parent flock primitive), ADR-dreaming-001 (Daydream Stop-hook — the cross-writer race ADR-021 has to close)

### Summary
ADR-020 (entry 3) declared the v2 Dream mutation PR blocked on a successor ADR. ADR-021 is that successor. It resolves all four open items in one document: (1) **mutation primitive** = `Router.delete(item_id) -> int` — the duck-typed fan-out across sqlite + markdown + graph backends Brent shipped in PR #93. (2) **Concurrency primitive** = Option A from ADR-020, a basedir-scope `flock` on `<basedir>/.dream.lock`, lifted shape-for-shape from ADR-014's per-session `_per_session_lock`. (3) **NFS / multi-machine** = no. Worker hard-fails on detected network FS; `DREAM_ALLOW_NETWORK_FS=1` exists only to escape misdetection. (4) **Cross-domain sign-offs** = Brent ack'd via PR #93's shipped contract; Keith forward-looking; Ken moot-pending-countersignature (Keith's bench process doesn't invoke `dream-cli` mid-bench, so `dream.lock_contended` measurement loss is moot — but the v2 mutation PR description must cite Ken's ack before merging).

The hard piece was the Shape-2 race — Daydream-vs-Dream. ADR-020 named two race shapes; the second is harder because Daydream is already a concurrent writer on a different code path. ADR-021 Decision 4: **Daydream waits on the basedir lock during a Dream sweep**. `engine.daydream()` acquires the basedir lock BEFORE the per-session lock, before state touch + sweep, before any store access — the load-bearing invariant pinned in the ADR. On contention emits `daydream.dream_in_progress_skipped` and returns clean (no state mutation, no cursor advance).

### Key state
The v2 Dream mutation half is **unblocked at the ADR layer**. Any Job 1-4 mutation PR can ship by citing ADR-021 + implementing the lock + delete call shape pinned here. ADR-020 is superseded (status flipped in frontmatter, body header, and the README index row). Worker contract: acquire `<basedir>/.dream.lock` before any `store.all()` / `Router.delete()` / `Router.write()`; on `_DreamLockHeld` emit `dream.lock_contended` and CLI exits 0; retire items via `Router.delete(item_id) -> int` (no winner-write-back); hard-fail on NFS with `_UnsupportedFsError`; `DREAM_ALLOW_NETWORK_FS=1` overrides with a warning. Daydream acquisition contract is the dual.

### Open items
- Job 1-4 mutation halves unblocked but unwritten. Job 1 mutation is the named next PR.
- Ken's countersignature on the `dream.lock_contended` moot status is a forward ask; the v2 mutation PR description must include it.
- `MemoryStore.delete()` `[CONTRACT]` PR named as Brent's follow-up — not a precondition for any Dream PR.
- Job 2 mutation primitive is bound: any Job 2 PR introducing consolidated-write-back / tombstone / CAS requires a successor ADR — cannot land under ADR-021's contract.

### Artifacts at time of entry
- [`docs/adrs/ADR-dreaming-021-dream-mutation-concurrency.md`](../docs/adrs/ADR-dreaming-021-dream-mutation-concurrency.md) — the new contract
- [`docs/adrs/ADR-dreaming-020-cross-process-dream-mutation-gate.md`](../docs/adrs/ADR-dreaming-020-cross-process-dream-mutation-gate.md) — superseded, retained as historical
- [`docs/adrs/README.md`](../docs/adrs/README.md) — index updated with both rows

### Notable since last entry
- Brent shipped PR #93 (`Router.delete` duck-typed across the 3 backends) the day before this arc — what made ADR-021's mutation-primitive answer concrete rather than aspirational. Without #93, Option B (Router/Store CAS) would have been the only realistic answer + a heavier cross-domain ask.
- Ken's PR #94 (drive prior sessions through daydream when seeding plugin-real) confirms the bench wiring fires daydream per-task — no `dream --all` mid-bench invocation needed from us, which mooted Ken's lock_contended sign-off ask.
- Workflow used 4 parallel research agents → synthesis → halliday APPROVED-WITH-AMENDMENTS (0 blockers, 7 amendments — all applied before commit, including the Ken-countersignature reframing that fixed an "unilateral moot" violation of ADR-020's amendment-honoring requirement).

---

## 2026-06-22T23:27 — entry 5

**Triggered by:** PR #98 merged — Job 1 mutation. The worker now retires dedup cluster losers via `self.store.delete()` under the basedir flock per ADR-021.
**Branch:** `dreaming/job1-mutation`
**Related ADRs:** ADR-dreaming-021 (implements its contract verbatim), ADR-dreaming-014 (parent flock primitive), ADR-harness-006 (fail-open at the CLI), ADR-dreaming-002 (one of its four jobs now both detection and mutation)

### Summary
The first **mutation** PR in the consolidation arc. Worker behavior delta: where v1 walked `store.all()` and returned a summary describing duplicates, v2 also picks a winner per cluster, calls `self.store.delete(retired_id)` on every loser, and reports the deletes in the summary dict. Winner-selection rule pinned in rubric §D5a/D5b — latest `item.timestamp` wins; ties broken by lexicographically lowest `item_id`. Summary dict gained `winner_id` + `retired_ids` per cluster + `items_retired` count + `mode = "detection_and_mutation"`. Lock + NFS contract is the ADR-021 shape: worker hard-fails on detected network FS; basedir flock acquired before any store access; `engine.daydream()` also acquires the basedir lock before its per-session lock and emits `daydream.dream_in_progress_skipped` on contention. CLI `_handle_dream` catches `_DreamLockHeld` and `_UnsupportedFsError` separately, each with its own event, still exit-0 fail-open.

The workflow surfaced a real lesson: jasnah's final grade returned **FAIL** with 3 blockers I'd missed — rubric §J1 import allow-list forgot stdlib `pathlib` and `logging` (both needed by the basedir-resolution + warning-log paths that the same rubric mandated), §L20's platform-dispatch test only patched `win32` (rubric demanded all three platforms), §L9/L10 ordering tests passed vacuously with a `MagicMock` client because Daydream's early-return path never reached the inner-block assertions. All three fixed before commit: rubric J1 amended to include the stdlib modules its own criteria require; L20 extended to mock `/proc/mounts` for Linux + `mount` shell-out for Darwin; L9/L10 fixtures use a stub `extract_memories` so the ordering assertion is non-vacuous.

### Key state
The full v1 Dream loop is now write-mutating. `daydream-cli dream --all` against a store with N items and K dedup clusters: returns a dict reporting K clusters + winners + retired losers, and the store has had `sum(len(retired_ids))` items hard-deleted via `Router.delete` fan-out. Brent's [CONTRACT] PR #99 (added `delete(item_id) -> bool` to the frozen `MemoryStore` protocol) landed while this PR was in flight — rebased cleanly: the rubric language about "duck-typed Router.delete" became historical; the `# type: ignore[attr-defined]` came off the worker's call site; the `Router.delete` carve-out in §J3 became just the frozen protocol's allowed surface. Integration smoke (real `RouterStore` via `build_store(tmp_path)`) catches the production-wiring case the 83 unit tests miss — they all use `_DeleteAwareStore(InMemoryStore)` and don't exercise Router's fan-out.

### Open items
- Job 2 (contradiction), Job 3 (governance), Job 4 (TTL pruning) — all unblocked at the ADR layer, none implemented. Job 4 is the named next arc (stdlib-only, no LLM dep).
- `MemoryStore.delete()` `[CONTRACT]` PR was the named follow-up from ADR-021 — it landed faster than expected (Brent PR #99 + #101), so no longer an open item.
- Stale-lock reclamation (ADR-014, ADR-021 explicit out-of-scope) untouched.
- Bench-runner invocation of `dream --all` between batches is Keith's lane per user's clarification — no ask outstanding.

### Artifacts at time of entry
- [`eval/memeval/dreaming/worker.py`](../eval/memeval/dreaming/worker.py) — mutation body
- [`eval/memeval/dreaming/_state.py`](../eval/memeval/dreaming/_state.py) — `_basedir_dream_lock`, `_DreamLockHeld`, `_UnsupportedFsError`, `_is_network_fs`
- [`eval/memeval/dreaming/engine.py`](../eval/memeval/dreaming/engine.py) — Daydream acquires basedir lock first
- [`eval/memeval/dreaming/cli.py`](../eval/memeval/dreaming/cli.py) — `_handle_dream` catches the new exception types
- [`eval/memeval/dreaming/tests/JOB1_MUTATION_RUBRIC.md`](../eval/memeval/dreaming/tests/JOB1_MUTATION_RUBRIC.md)
- [`eval/memeval/dreaming/tests/test_worker_mutation.py`](../eval/memeval/dreaming/tests/test_worker_mutation.py) — 83 unit tests + 1 integration smoke
- INITIAL_DREAM_RUBRIC.md retained as historical with "Superseded by JOB1_MUTATION_RUBRIC.md" header; test_worker.py removed (its detection-only contract is overturned)

### Notable since last entry
- Brent's PR #99 ([CONTRACT] `delete` in frozen `MemoryStore` protocol) + PR #101 (consumer audits) landed during this PR — rebased clean and rubric language updated.
- The "jasnah FAIL on missing stdlib in §J1" is the second time the same shape has surfaced: the rubric author writes a criterion that constrains the impl in a way the rubric's own other criteria require to violate. Worth pinning as a check at rubric-authoring time, not at impl time.
- Workflow stayed disciplined despite the late jasnah FAIL: 3 blockers fixed before commit, not deferred.

---

## 2026-06-23T00:11 — entry 6

**Triggered by:** PR #103 merged — Job 4 (TTL pruning) detection + mutation. Layered on top of Job 1 mutation.
**Branch:** `dreaming/job4-ttl-pruning`
**Related ADRs:** ADR-dreaming-021 (inherits its lock + NFS + delete primitives), ADR-dreaming-015 (orthogonal — `sweep_old_state` prunes filesystem state files; Job 4 prunes `MemoryItem` rows; deliberately separate env-var surfaces)

### Summary
The fourth and final stdlib-only job — the LLM-driven jobs (2 contradiction, 3 governance) are the remaining arc. Job 4 layers TTL pruning INTO the existing `DreamingWorker.run()` body: before clustering, items whose age (`now - item.timestamp`) exceeds `DREAM_ITEM_RETENTION_DAYS` are dropped via the same `self.store.delete()` primitive. The pinned design decisions surface as 13 Open-contracts pins in the rubric preamble; the load-bearing ones: env var is `DREAM_ITEM_RETENTION_DAYS` (not `DREAM_RETENTION_DAYS` — that name is already taken by ADR-015's `_read_ttl_days` for filesystem-state TTL, and reusing it would silently couple two orthogonal surfaces). TTL boundary is strictly greater (`age > retention_seconds`); equal is NOT pruned. `DREAM_ITEM_RETENTION_DAYS=0` DISABLES pruning, not "prune everything" — footgun protection. `item.timestamp == 0.0` IS pruned (timestamp 0.0 is either a synthetic fixture or a write-path bug — both better surfaced by deletion than silent immortality). TTL-first ordering inside the basedir lock: pruning removes stale items that would otherwise become recency winners under §D5a; clustering sees a smaller working set; `items_pruned` is independent of dedup outcomes.

Second arc where jasnah's final grade returned FAIL — same shape as Job 1 mutation, different specifics. 14 named rubric tests missing was the load-bearing one; jasnah's "Coverage Rubric self-check via `comm -23` BEFORE grading" follow-up is now baked into the Job 2 handoff. The D-TTL-5 rubric prose was internally inconsistent — described an item with a *later* timestamp than its sibling that's also past TTL while the sibling is fresh, which is impossible. Reframed around `items_retired == 0` as the load-bearing TTL-first-vs-dedup-first distinguisher. The J-TTL-6 grep was over-strict: literal `! grep -nE 'sweep_old_state'` false-positives on legitimate docstring references to ADR-015's naming pattern; converted to AST-based check. All three categories fixed before commit; 61 unit + 1 integration smoke now passing with full rubric-test name parity.

### Key state
With Jobs 1 and 4 shipped, the worker executes: trajectories guard → NFS detection → basedir flock → `store.all()` → TTL prune → cluster survivors → dedup retire → summary → emit. Summary dict mode = `"detection_and_mutation_and_pruning"`. `jobs_run` = `["dedup_detection", "dedup_merge", "ttl_pruning"]`. `skipped_jobs` = `["contradiction_resolution", "governance"]`. Counts adds `items_pruned` + `retention_seconds_effective`. New top-level `pruned: {item_ids, retention_seconds_effective}` block. The `dream.summary` event carries all six numeric fields. Superseded Job 1 rubric criteria (§B4 mode, §B5 jobs_run, §B6 skipped_jobs, §B7 counts) are formally folded — JOB4_TTL_RUBRIC supersedes those literals; the 5 corresponding Job 1 tests are removed; an autouse `_disable_ttl_in_job1_tests` fixture in `test_worker_mutation.py` sets `DREAM_ITEM_RETENTION_DAYS=0` so the Job 1 tests' synthetic timestamps (1.0, 2.0, etc — 55+ years old at real `time.time()`) don't get eaten by TTL pruning.

### Open items
- Job 2 (contradiction, LLM-driven) — next arc. Reuses the OpenRouterClient from Daydream's chunk-extraction.
- Per the bench-direction analysis earlier this session, Jobs 2 and 3 have NO first-class signal on SWE-Bench-CL (no contradiction labels, no importance gradient), but user mandated "build out the entire Dream function" per PRD regardless.
- Job 3 (governance: must-know / must-do / blacklist) — after Job 2. Same LLM call surface.
- Jasnah's Job-2-specific follow-ups (captured in `project_job2_contradiction_handoff.md` auto-memory): rubric Coverage Self-check via `comm -23` BEFORE grading; AST-based instead of grep-based non-coupling checks; pre-name the 4 I4 preservation tests; concurrency ordering matrix (3 mutation passes now); disjointness invariants as a pairwise check.
- Stale-lock reclamation (ADR-014) still untouched.

### Artifacts at time of entry
- [`eval/memeval/dreaming/worker.py`](../eval/memeval/dreaming/worker.py) — Jobs 1 + 4 layered
- [`eval/memeval/dreaming/tests/JOB4_TTL_RUBRIC.md`](../eval/memeval/dreaming/tests/JOB4_TTL_RUBRIC.md)
- [`eval/memeval/dreaming/tests/test_worker_ttl.py`](../eval/memeval/dreaming/tests/test_worker_ttl.py) — 61 unit + 1 integration smoke
- [`eval/memeval/dreaming/tests/test_worker_mutation.py`](../eval/memeval/dreaming/tests/test_worker_mutation.py) — autouse `_disable_ttl_in_job1_tests` + 5 superseded literal tests removed
- INITIAL_DREAM_RUBRIC.md + JOB1_MUTATION_RUBRIC.md retained as historical context

### Notable since last entry
- Two arcs in 24 hours (Job 1 mutation entry 5 + Job 4 entry 6) — workflow shape proved repeatable, including the jasnah-FAIL-on-rubric-coverage pattern. Pattern now documented as a Job-2 pre-grade check.
- Brent's storage roadmap stayed active in parallel: PR #99/#101 (frozen-protocol delete), PR #102 (MEMORY_VERSION-keyed root). None coupled to Dream's worker changes — verified by inspection.
- The user flagged a session-management lesson at the end of this arc: 4 PRs in one session is a real context-load risk; precision degrades after compression. Pausing for fresh-session handoff to Job 2 with the brief committed to auto-memory.


---

## 2026-06-23T01:30 — entry 7

**Triggered by:** PR #105 merged — Job 2 (contradiction resolution, LLM-driven). Third of four ADR-002 jobs shipped; only Job 3 (governance) remains.
**Branch:** `dreaming/job2-contradiction`
**Related ADRs:** ADR-dreaming-002 (the four-jobs contract — §Open-items "Job 2 (contradiction resolution) worker shape" now closed-by-execution), ADR-dreaming-021 (§Open-items lines 568–573 amended in-place to CLOSED as the implementation PR's closure_artifact; per the ADR's own "amend OR addendum" alternative), ADR-dreaming-006/010/012/013 (inherited unchanged — LLMClient seam, RedactedText boundary, missing-key fail-open, cursor non-advance pattern adapted as "no mutation when batch returns empty").

### Summary
Job 2 shipped the LLM-driven contradiction pass: `DreamingWorker.run()` now layers a contradiction step AFTER dedup, inside the same basedir flock. Surviving items are batched (K=10, non-overlapping, hour-bucketed shuffle seed) through `_make_llm_client()` (default `OpenRouterClient`); the LLM judges only WHETHER pairs contradict — the worker picks the loser deterministically (latest `item.timestamp` wins; lex-lowest `item_id` tiebreaks — same rule as Job 1 §D5a/D5b). Loser retired via `self.store.delete()`; no new mutation primitive, no consolidated write-back, no successor ADR required. Hard cap `DREAM_CONTRADICTION_MAX_CALLS` (default 20) bounds spend; `=0` disables (footgun protection, matches Job 4 §H-TTL-2 symmetry). Cost-observability extends the summary `counts` with six new fields (`items_contradicted`, `contradiction_llm_calls`, `contradiction_input_tokens`, `contradiction_output_tokens`, `contradiction_cost_usd_estimate` — the single float key, naturally; `contradiction_pairs_examined_estimate`); new top-level `contradicted: {pairs, model}` block parallel to `pruned` and `clusters`. Per-batch `dream.contradiction_batch_complete` event for runaway-spend detection (halliday A1).

The third arc — and the FIRST time jasnah's final grade came back PASS on the first attempt. 160/160 criteria PASS, 0 blockers, 0 N-A, coverage gate clean. What changed: halliday's adversarial review was done in PARALLEL with jasnah's rubric authoring, and halliday's 5 BLOCKERS + 5 amendments were applied INLINE to BOTH the plan AND the rubric BEFORE implementation began. (Halliday's findings were all load-bearing: tag/item_id redaction trust boundary B1 — original plan only redacted `content`; ADR-021 §Open-items closure_artifact B2; `_session_id_for_dream` shim definition B3; cap-reached event semantics for the `max_calls=0` disabled path B4; disjointness as a hard `raise RuntimeError` not `assert` B5 — assertions disappear under `python -O`.) The rubric-amendment subagent applied 11 inline diffs to the 916-line rubric in parallel with the implementation; the test-writing subagent produced a 2624-line test suite mirroring the rubric criterion-by-criterion. Coverage gate self-check via `comm -23` ran clean on first try.

CodeRabbit then surfaced a real correctness bug post-PR-open: `_disjointness_check` ran AFTER the worker had already deleted contradiction-losers, so a dedup `cluster_winner` becoming a contradiction-loser would delete the cluster's only surviving representative — leaving the store in a partial-mutation state when the post-delete invariant check raised. Fixed via a `protected_ids` kwarg on `_detect_contradictions` that extends the within-pass winner-collision drop to include cross-pass cluster_winners; the §C-J2-disjoint invariant now holds BY CONSTRUCTION pre-delete. Two tests added in the follow-up commit (`test_cluster_winner_protected_from_contradiction_loss` integration + `test_detect_contradictions_protected_ids_kwarg` unit). Honest credit to CodeRabbit for catching a bug halliday's review didn't surface — adversarial review at multiple altitudes pays.

### Key state
With Jobs 1, 4, and 2 shipped, the worker's `run()` body executes: trajectories guard → NFS detection → basedir flock → `store.all()` → TTL prune (Job 4) → cluster survivors → dedup retire (Job 1) → contradiction batch + retire (Job 2) → pairwise-disjoint hard check → summary dict → `dream.summary` emit. Summary `mode = "detection_and_mutation_and_pruning_and_contradiction"`. `jobs_run = ["dedup_detection", "dedup_merge", "ttl_pruning", "contradiction_resolution"]`. `skipped_jobs = ["governance"]`. The disjointness invariant `pruned_ids ⊥ retired_ids ⊥ contradicted_loser_ids ⊥ all_winners` holds by construction at every pass boundary thanks to the working-set shrinkage pattern (`survivors = items - pruned_set - retired_ids_set`) plus the cross-pass `protected_ids` carve-out. New event family on the worker: `dream.contradiction_batch_complete`, `dream.contradiction_pair_dropped_winner_collision`, `dream.contradiction_invalid_id_dropped`, `dream.contradiction_skipped_unavailable_llm`, `dream.contradiction_batch_parse_failed`, `dream.contradiction_partial_parse`, `dream.contradiction_call_cap_reached` (allow-set of 8 total including `dream.summary`). `CONTRADICTION_SYSTEM_PROMPT` is sha256-pinned at `25cd0ad0…` alongside the existing `EXTRACTION_SYSTEM_PROMPT` pin; the prompt schema asks the LLM for `{"pairs":[{"a_id","b_id","rationale"}]}` (Pushback A — never asks LLM to label loser/winner since the worker overrides). Hour-bucketed shuffle seed `sha256(session_id || hour_bucket)[:16]` means coverage genuinely varies across runs over time; at default config (10k items, K=10, max_calls=20), per-run coverage is ~0.0018% of pair-space — honestly named in the rubric preamble per halliday A2.

### Open items
- **Job 3 (governance: must-know / must-do / blacklist)** — the last ADR-002 job. Same LLM-call surface as Job 2; the workflow shape proved on Jobs 1+4+2 transfers directly. Expected to add a third named envelope wrapper, which is why §J-J2-envelope was rewritten to assert by NAME (not by COUNT) — Job 3 can land its wrapper without re-grading Job 2.
- **No SWE-Bench-CL signal** is expected from Jobs 2 or 3 (per `wsybac234` bench-direction analysis); user mandated "build out the entire Dream function" per PRD regardless. Rubric preamble pins this acknowledgement.
- **LLM-judgment trust posture** — v1 accepts LLM mis-deletions as observable (via `summary.contradicted.pairs[].rationale`) but recoverable only manually. Future work could add a sample-verify pass or human-in-loop confirmation; not in scope for v1.
- **Cross-batch contradictions deliberately missed** — sliding-window K=10 cannot detect contradictions spanning two batches in a single run. Coverage accumulates over runs via the hour-bucket shuffle seed; if a future operator needs synchronous full-coverage, that's a successor design.
- **Stale-lock reclamation** (ADR-014) still untouched.
- **The `_disable_ttl_in_job1_tests` autouse fixture** in `test_worker_mutation.py` extended its scope to Job 2's tests via a parallel `_disable_ttl_for_contradiction_tests` autouse fixture; both pin `DREAM_ITEM_RETENTION_DAYS=0` so synthetic timestamps survive TTL. Cleanup if/when Job 4's TTL semantics ever extend to "modern" timestamps in the fixtures.

### Artifacts at time of entry
- [`eval/memeval/dreaming/worker.py`](../eval/memeval/dreaming/worker.py) — Jobs 1 + 4 + 2 layered (~720 lines)
- [`eval/memeval/dreaming/prompts.py`](../eval/memeval/dreaming/prompts.py) — `EXTRACTION_SYSTEM_PROMPT` + `CONTRADICTION_SYSTEM_PROMPT` + shared `_ENVELOPE_TEMPLATE`
- [`eval/memeval/dreaming/tests/JOB2_CONTRADICTION_RUBRIC.md`](../eval/memeval/dreaming/tests/JOB2_CONTRADICTION_RUBRIC.md) — 916 lines, 160+ boolean criteria, 14 sections + Pushback resolutions appendix
- [`eval/memeval/dreaming/tests/test_worker_contradiction.py`](../eval/memeval/dreaming/tests/test_worker_contradiction.py) — 2624 lines, 148 tests (146 + 2 CodeRabbit-fix)
- [`eval/memeval/dreaming/tests/test_prompts.py`](../eval/memeval/dreaming/tests/test_prompts.py) — sha256 pin + 7 substring contracts
- [`docs/adrs/ADR-dreaming-021-dream-mutation-concurrency.md`](../docs/adrs/ADR-dreaming-021-dream-mutation-concurrency.md) — §Open-items lines 568–573 closed in-place 2026-06-23
- INITIAL/JOB1/JOB4 rubric files retained as historical context for the layered supersession chain

### Notable since last entry
- **Jasnah PASS first try** — the workflow's biggest learning. Halliday-blockers-applied-to-rubric-BEFORE-impl is the load-bearing difference vs Jobs 1+4 (both FAIL'd on missing tests + rubric prose inconsistencies). Worth carrying forward to Job 3.
- **Subagent parallelization paid off** twice: the rubric-amendment subagent worked in parallel with worker.py implementation, and the test-writing subagent produced the test suite while I worked on coverage-gate prep + ADR closure_artifact + test_extract.py audit-test update.
- **CodeRabbit found a real bug** halliday missed (cluster_winner-as-contradiction-loser). Multiple adversarial layers at different altitudes (halliday on architecture pre-impl, jasnah on rubric coverage, CodeRabbit on diff-level correctness) caught different classes of defects. Don't skip any.
- **The dispatcher Pushback packet pattern worked** — jasnah's rubric draft surfaces 10 named Pushbacks (A through K minus B which was self-resolved); dispatcher accept/reject is captured in the rubric body. Job 3 should inherit this packet shape.
- **§B8 dispatcher amendment** — `contradiction_cost_usd_estimate` is float (`cost.cost_of` returns float); preserving USD precision is more honest than the int alternatives. Amendment was made before jasnah graded and recorded as a binding pin.

---

## 2026-06-23T06:30 — entry 8

**Triggered by:** PR #107 ready to merge — Job 3 (governance: must_know / must_do / blacklist, LLM-driven). **ALL FOUR ADR-002 jobs landed.**
**Branch:** `dreaming/job3-governance`
**Related ADRs:** ADR-dreaming-002 (§Open-items "Consolidation logic" CLOSED 2026-06-23 as this PR's closure_artifact — all four jobs now have execution closures; the four-jobs contract is fulfilled), ADR-dreaming-021 (§Policy honored without amendment — only blacklist mutates, via the bound `self.store.delete` primitive; no successor ADR was needed end-to-end for any of the four jobs).

### Summary
The fourth and final ADR-002 job. The worker `run()` body's final shape: trajectories guard → NFS detection → basedir flock → `store.all()` → TTL prune (Job 4) → cluster survivors → dedup retire (Job 1) → contradiction batch + retire (Job 2) → governance batch + classify + advisory backstop + blacklist retire (Job 3) → 5-set pairwise disjoint check → summary dict → single `dream.summary` emit. Summary `mode = "detection_and_mutation_and_pruning_and_contradiction_and_governance"`. `jobs_run` has five entries; `skipped_jobs = []` (empty list — the four-jobs contract is fulfilled).

Job 3 ships per-item classification via a single batched LLM call returning `{"classifications":[{"item_id","class","rationale"}]}` where `class ∈ {none, must_know, must_do, blacklist}`. Only `blacklist` mutates (via `self.store.delete`, same primitive as Jobs 1+4+2 — no new mutation primitive, no successor ADR required). `must_know` and `must_do` are **SOFT** — surfaced in the new top-level `governance: {must_know, must_do, blacklisted, model}` summary block; **no recall consumer reads it in v1** (pinned as FORENSIC-ONLY by halliday B4 with rubric §K31 asserting no recall consumer exists via AST scan). Hard cap `DREAM_GOVERNANCE_MAX_CALLS` (default 20; `=0` disables). Hour-bucketed shuffle seed with `gov` discriminator. New `GovernanceTag` NamedTuple has 3 fields internally (`item_id`, `rationale`, `batch_index`) — `batch_index` is projected out at summary construction time (§B16 fixes summary entries to `{item_id, rationale}`) but used in the per-id `dream.governance_blacklisted` audit emit. `_resolve_governance_collisions` applies cross-class precedence (`must_know > must_do > blacklist`) → protected-id drops (only for blacklist; ids targeting cluster_winners or contradiction_winners) → within-class first-seen dedup, in that order — all emitting a single unified `dream.governance_classification_dropped` event with `reason ∈ {collision, protected}`.

**Second consecutive jasnah PASS-first-try.** 944 dreaming tests pass / 9 skip / 0 fail. The workflow pattern is validated: halliday's 6 BLOCKERS + 8 amendments were applied INLINE to BOTH the plan AND the rubric BEFORE implementation began (the rubric-amendment subagent applied diffs to the 1279-line rubric in parallel with worker.py coding). 14 dispatcher Pushbacks all ACCEPTED (Pushback B was OVERRIDDEN by halliday B2 — protected-drop moved from per-batch loop into the resolver; unified single event replaces the original two parallel events).

The test-writing subagent caught a real bug post-impl: my first draft of the advisory backstop ran AFTER the delete loop, so a `must_know ⊥ blacklist` violation would still delete the offending id (then "drop" it from the summary). Test `test_governance_advisory_invariant_violated_emits_and_drops_blacklist` failed; fix moved the backstop pre-delete so violating ids never reach `self.store.delete`. The §C-J3-disjoint invariant now holds BY CONSTRUCTION pre-delete — same correctness pattern as the Job 2 CodeRabbit-caught cluster_winner-as-contradiction-loser bug. Two-layer adversarial review (halliday pre-impl + test-writing-subagent during impl) caught defects at different altitudes; the established Job 2 + Job 3 pattern of test-by-test rubric construction surfaces correctness issues that pure plan-review misses.

CodeRabbit on the PR returned ONE minor finding (🟡 / Quick win — hallucinated-id rows don't increment `n_dropped` in `dream.governance_partial_parse` accounting). Deferred: the rubric semantically separates `partial_parse` (structural malformed entries) from `invalid_id_dropped` (hallucinated ids with their own dedicated event); Job 2's contradiction implementation has the identical pattern. Acting on the finding would conflate two distinct failure modes and diverge Job 3 from Job 2.

### Key state
With all four jobs shipped, the worker has stable contracts across the entire pipeline. The 5-set mutation-disjoint invariant (`pruned_ids ⊥ retired_ids ⊥ contradicted_loser_ids ⊥ blacklisted_ids ⊥ all_winners`) holds by construction at every pass boundary via the working-set shrinkage pattern + the cross-pass `protected_ids` carve-outs (Job 2 introduced for contradiction; Job 3 reuses for blacklist). Advisory sets (`must_know_ids`, `must_do_ids`) are deliberately NOT in the hard-disjoint check — they're allowed to overlap with each other AND with winners. The advisory backstop runs pre-delete to catch resolver-refactor drift on the `must_* ⊥ blacklisted` constraint (emits `dream.governance_advisory_invariant_violated`, drops the offending blacklist before mutation). Total event family on the worker: 18 names = Job 2's 8 (1 summary + 7 contradiction) + Job 3's 10 (1 batch_complete + 1 skipped + 1 parse_failed + 1 partial_parse + 1 call_cap + 1 unified classification_dropped + 1 invalid_id + 1 per-id blacklisted + 1 delete_failed + 1 advisory_invariant). `GOVERNANCE_SYSTEM_PROMPT` sha256-pinned at `212a982108…` alongside `EXTRACTION_SYSTEM_PROMPT` and `CONTRADICTION_SYSTEM_PROMPT`. Three named envelope wrappers across the dreaming module: `_wrap_user_content_in_envelope` (Daydream), `_wrap_batch_in_envelope` (Job 2 contradiction), `_wrap_governance_batch_in_envelope` (Job 3). The `test_extract.py:679-720` AST audit asserts by NAME (not by COUNT) — Job 2 set this up specifically so Job 3 could land its wrapper without re-grading Job 2.

### Open items
- **Recall-side enforcement of must_know / must_do** is the natural v2 follow-up. The advisory consumer contract is FORENSIC-ONLY v1 (halliday B4 pinned this; §K31 enforces). A future PR could add a recall path that boosts `item.relevancy` on `must_know` ids or surfaces `must_do` items prominently — but that'd touch `MemoryItem` mutation (write primitive) and would require a successor ADR per ADR-021 §Policy. Not in scope here.
- **Bench signal for the LLM-driven jobs (2 + 3)** remains absent. Per the bench-direction analysis (`wsybac234`), governance has no first-class signal in SWE-Bench-CL. Acknowledged in the rubric preamble; not a defect.
- **Mode literal scaling** — halliday B1 flagged that `_and_`-stacking the mode literal across four jobs hit 67 chars. Since Job 3 IS the last ADR-002 job, this is acceptable v1. A v2 / ADR-022-style addition would need to either truncate or restructure.
- **CodeRabbit finding (n_dropped accounting for hallucinated ids)** deferred — rubric pins partial_parse and invalid_id_dropped as distinct event surfaces; Job 2 has the same separation.
- **Stale-lock reclamation** (ADR-014) — still untouched.
- The `_disable_governance_for_contradiction_tests` autouse fixture in test_worker_contradiction.py + `_disable_contradiction_for_governance_tests` in test_worker_governance.py + `_disable_ttl_in_job1_tests` in test_worker_mutation.py — three layers of test-isolation autouse fixtures now stacking on top of each other. Cleanup-worthy if/when the test suites move to an integration shape, but the per-job isolation is honest for unit-grading.

### Artifacts at time of entry
- [`eval/memeval/dreaming/worker.py`](../eval/memeval/dreaming/worker.py) — Jobs 1 + 4 + 2 + 3 layered (~1290 lines)
- [`eval/memeval/dreaming/prompts.py`](../eval/memeval/dreaming/prompts.py) — `EXTRACTION_SYSTEM_PROMPT` + `CONTRADICTION_SYSTEM_PROMPT` + `GOVERNANCE_SYSTEM_PROMPT` + shared `_ENVELOPE_TEMPLATE`
- [`eval/memeval/dreaming/tests/JOB3_GOVERNANCE_RUBRIC.md`](../eval/memeval/dreaming/tests/JOB3_GOVERNANCE_RUBRIC.md) — 1363 lines, 160+ boolean criteria, 14 sections + Pushback resolutions appendix
- [`eval/memeval/dreaming/tests/test_worker_governance.py`](../eval/memeval/dreaming/tests/test_worker_governance.py) — 3315 lines, 183 tests
- [`eval/memeval/dreaming/tests/test_prompts.py`](../eval/memeval/dreaming/tests/test_prompts.py) — extended with 7 governance pin / substring / class-enum tests
- [`eval/memeval/dreaming/tests/conftest.py`](../eval/memeval/dreaming/tests/conftest.py) — NEW session-scope LLM seam guard (rubric §N20)
- [`docs/adrs/ADR-dreaming-002-dreaming-consolidation-cli.md`](../docs/adrs/ADR-dreaming-002-dreaming-consolidation-cli.md) — §Open-items "Consolidation logic" closed in-place 2026-06-23; closure block enumerates all four jobs
- Job 1/2/4 rubric files retained as historical context for the layered supersession chain (INITIAL → JOB1 → JOB4 → JOB2 → JOB3)

### Notable since last entry
- **Second consecutive jasnah PASS first try.** Workflow pattern (apply halliday blockers to BOTH plan AND rubric BEFORE impl, via rubric-amendment subagent in parallel with worker coding) is validated across two consecutive substantive PRs. Worth carrying forward to any future Dream-adjacent arc.
- **The test-writing subagent caught a real correctness bug** halliday's pre-impl review missed (advisory backstop ordering — would have left store mid-mutation on a refactor-induced advisory violation). This is the second time multi-altitude adversarial review caught a bug at a different layer than halliday: Job 2 = CodeRabbit caught cluster_winner-as-contradiction-loser; Job 3 = test-writing subagent caught backstop-after-delete. Halliday + jasnah + CodeRabbit + test-writing-subagent each surface different defect classes. Don't skip any.
- **Pre-merge `/kb` workflow** (folded into the impl PR per the updated `feedback_substantive_tasks_workflow` memory) — this entry IS the first to land via the new pattern. Job 2's pending entry (drafted post-merge, staged in `pending_kb_entry_job2_dreaming.md` auto-memory) rides on THIS PR alongside the Job 3 entry; the pending memory will be deleted post-merge.
- **ADR-002 §Open-items closure** is the first ADR closure_artifact in the dreaming arc to be marked by execution alongside an enumeration of the closure work (not just a pointer). Pattern worth carrying for future ADR closures.
- **Three new envelope wrappers across the dreaming module** — `test_extract.py` 3-wrapper audit was correctly positioned by Job 2 (by-NAME assertion) so Job 3 added its wrapper without re-grading the audit shape.
- **Halliday B4 pin (must_know/must_do as forensic-only v1)** is the most architecturally-loaded amendment in the four-job arc. Cuts a clean line between "land the data path correctly" and "wire up downstream consumers" — keeping the v1 scope honest about what's actually integrated end-to-end.

---

## 2026-06-23T08:30 — entry 9

**Triggered by:** PR #108 ready to merge — Daydream selective extraction (moderate threshold + per-candidate rejection event surface).
**Branch:** `daydream/selective-extraction`
**Related ADRs:** ADR-dreaming-005 (Daydream JSONL ingestion + inline redaction — threshold refinement only, no contract change), ADR-dreaming-006/010/011/012/013 (LLMClient, RedactedText boundary, redaction audit, missing-key fail-open, cursor non-advance — all preserved without amendment), ADR-dreaming-009 (events shim — new event names accepted without amendment per its shape contract). **No new ADR.**

### Summary
First post-arc Daydream-side PR. The previous five dreaming PRs (#88/#98/#103/#105/#107) all landed Dream-side (whole-store consolidation) jobs; this one finally moves to the Daydream-side (in-session capture) and addresses the "store everything we can extract" posture that's been the pipeline's default since PR #88. The new posture is MODERATE selectivity at write-time: emit a memory only if "would a future session benefit from this fact?" is clearly yes; durable facts + decisions-with-rationale + ongoing commitments qualify; transient chatter, command echoes, narration, and tentative musings drop. The LLM additionally emits a parallel `rejected: [...]` array carrying the considered-but-dropped candidates with rationale, surfaced as a new `daydream.candidate_rejected` event per row — operators read the diary to calibrate the prompt. Daydream layer is INDEPENDENT of Job 3 governance — Daydream filters per-session at write-time; Job 3 classifies whole-store at sweep-time; cross-layer contamination is guarded by both positive AND negative substring contracts on the prompt (`must_know`/`must_do`/`blacklist`/`classifications`/`pairs`/`a_id`/`b_id` all FORBIDDEN).

**Third consecutive jasnah PASS-first-try.** The workflow pattern (apply halliday's BLOCKERS to BOTH plan AND rubric INLINE BEFORE impl, via the rubric-amendment subagent in parallel with worker coding) is robustly validated now across three arcs (Job 2 + Job 3 + this).

Halliday's 3 BLOCKERS were all load-bearing and all applied inline. The biggest: **B1 — `content_snippet` violates the ADR-005 redaction trust boundary**. The LLM is asked to quote candidate content verbatim in the rejection-event snippet; ADR-005 guarantees redaction on the INPUT side only; without a second pass, the diary becomes a forensic oracle of what redaction missed at ~3000 events/day. Fix: module-top `from memeval.dreaming.redaction import redact`; second-pass `redact()` on `content_snippet` BEFORE truncation + emit; ordering pinned by rubric §K. **B2 — unbounded event volume** under chatter-heavy transcripts. Fix: `_REJECTION_MAX_PER_CHUNK = 50`; overflow rows fold into `rejected_n_dropped` (additive extension to the existing `chunk_partial_parse` event); the prompt advertises the cap so the LLM self-selects. **B3 — silent regression masking** if a future model release stops emitting `rejected`. Fix: `_REJECTED_MISSING` sentinel distinguishes missing-key from `[]` / wrong-type; new `daydream.rejected_field_missing` event fires ONCE per `session_id` (module-level `_rejected_missing_seen: set[str]`).

The rationale field is DELIBERATELY NOT redacted (rubric §K4 pins this). Halliday's original B1 framing distinguished snippet (verbatim LLM quote of candidate content, high risk) from rationale (LLM-authored prose ABOUT the drop, lower risk). CodeRabbit flagged this on the PR as a B1-equivalent for rationale; dispatcher deferred per §K4. The asymmetry is intentional and rubric-pinned.

### Key state
The full Daydream-side event family now has six names: `chunk_skipped_unavailable_llm`, `chunk_skipped_parse_failed`, `chunk_partial_parse` (extended additively with `rejected_n_kept` / `rejected_n_dropped` kwargs while preserving the existing `n_kept`/`n_dropped` — backward-compatible), `daydream.chunk_extracted` (preserved), `daydream.candidate_rejected` (new, 6 kwargs including `snippet_truncated`/`rationale_truncated` per halliday A2), `daydream.rejected_field_missing` (new, 1 kwarg, one-shot per session). `EXTRACTION_SYSTEM_PROMPT` sha256 rotated to `b2f8f69bcff40693…` and propagated to all three pin sites (`test_extract.py:43`, `test_prompts.py:89`, `test_worker_governance.py:2089`). The Job 3 `test_extraction_prompt_unchanged_by_job3` assertion is now a semantic "Daydream prompt unchanged since the most recent rotation" check — the assertion still has drift-detection value, just bound to a new baseline. Three named envelope wrappers across the dreaming module remain (Daydream / contradiction / governance); no new wrapper added by this arc.

### Open items
- **CodeRabbit B1-equivalent for rationale field** — deferred per rubric §K4 + halliday's original framing. If a future regression shows rationale CAN quote candidate secrets in practice, the §K4 pin becomes the rubric to amend (would need a new architectural review).
- **CodeRabbit §K1 AST tightening nitpick** — current §K1 test asserts a `redact()` call exists in `_extract.py`; CodeRabbit's tightening would verify the actual snippet variable flows from `redact()`. Strict-enough check today; tightening is low-value.
- **The `_rejected_missing_seen` module-state** is process-local. Cleared by restart. The B3 one-shot guard is per-process, so a regression that persists across restarts fires one event per restart per session. Acceptable: process restart is itself an operator-visible event.
- **Substring contract is necessary but not sufficient** for "moderate selectivity" — the prompt could pass the substring contract while the LLM still over-emits or under-emits. The rubric preamble explicitly pins this: judgment quality is NOT unit-graded; surfaced via the diary post-PR.
- **Future "judgment quality" eval surface** — the rejection-event stream IS the calibration surface. A v2 bench-direction PR could attach a synthetic transcript fixture and measure keep:reject ratio drift across model versions. Not in scope here.

### Artifacts at time of entry
- [`eval/memeval/dreaming/prompts.py`](../eval/memeval/dreaming/prompts.py) — `EXTRACTION_SYSTEM_PROMPT` rewritten (3688 chars, sha256 `b2f8f69bcff40693…`); MODERATE threshold + concrete INCLUDE/REJECT examples + 50-cap advertised + negative-substring guards against Job 2 + Job 3 vocab
- [`eval/memeval/dreaming/_extract.py`](../eval/memeval/dreaming/_extract.py) — `extract_memories` extended with rejection-parse loop, second-pass redact, missing-key sentinel, overlap suppression, truncation flags; module-top `from memeval.dreaming.redaction import redact`
- [`eval/memeval/dreaming/tests/DAYDREAM_SELECTIVE_RUBRIC.md`](../eval/memeval/dreaming/tests/DAYDREAM_SELECTIVE_RUBRIC.md) — ~1100 lines, ~95 criteria + halliday §K/L/M/N amendments + CodeRabbit prose-drift fixes
- [`eval/memeval/dreaming/tests/test_extract.py`](../eval/memeval/dreaming/tests/test_extract.py) — +105 tests (1823 total lines)
- [`eval/memeval/dreaming/tests/test_prompts.py`](../eval/memeval/dreaming/tests/test_prompts.py) — sha256 pin rotated
- [`eval/memeval/dreaming/tests/test_worker_governance.py`](../eval/memeval/dreaming/tests/test_worker_governance.py) — sha256 pin at line 2089 rotated

### Notable since last entry
- **First post-ADR-002-arc PR.** All four Dream-side jobs (1+4+2+3) landed in entries 5-8; this is the first PR that's NOT one of the ADR-002 four jobs but is still on the dreaming workstream.
- **Third consecutive jasnah PASS first try.** Workflow pattern robustly validated.
- **Cross-PR sha256-pin coordination dance** — when a prompt's sha256 is pinned across MULTIPLE test files, every prompt-change PR touches every pin site. Three layers of drift detection at the cost of tedium. Lesson for the next prompt-touching PR.
- **Halliday's B1 framing of snippet vs rationale risk** generalizes: any future LLM-emitted field that's asked to quote candidate content is a residual-leak surface; fields asked for reasoning are not. Worth carrying.
- **CodeRabbit caught zero correctness bugs** on this PR. Jobs 2 + 3 each had real CodeRabbit-caught bugs (cluster_winner-as-contradiction-loser; advisory backstop ordering); this PR had only 1 rubric-pinned-defer + 2 prose drift + 1 nitpick. Possible interpretations: cleaner code from established workflow; tighter test surface; or statistical noise. Worth watching across more arcs.

---

## 2026-06-23T21:40 — entry 10

**Triggered by:** PR #116 opened — `DREAM_MODEL=deepseek/deepseek-v4-flash` test + ADR-dreaming-022 + observability gap closure. First post-ADR-002-arc PR on the model-side of the daydream surface.
**Branch:** `dreaming/deepseek-model-swap-test`
**Related ADRs:** ADR-dreaming-022 (new — test the deepseek model via `.env` override, fix stale `.env.example` docstring, add `llm_call_succeeded` observability event). ADR-dreaming-004 (default-model decision — PRESERVED without amendment; this PR does NOT promote deepseek to default). ADR-dreaming-012 (missing-key fail-open — relied upon as the safety net for any unreachable-model scenario).

### Summary
First swap-test of an alternative daydream subconscious model. The PR introduces opt-in support for `DREAM_MODEL=deepseek/deepseek-v4-flash` via the local `.env` while keeping `inclusionai/ling-2.6-flash` (ADR-dreaming-004) as the codebase-baked default. The goal was twofold per Scott: (a) actually exercise an alternative model on real Daydream calls to inform the bigger prompt-variant research arc, and (b) verify that the `.env` wiring flows end-to-end to every entrypoint that the daydream function fires from. Both achieved; the live OpenRouter smoke confirmed `deepseek/deepseek-v4-flash` is reachable (returned `'hello'`, 18 tokens out) and the PR is green on all automated checks (CI test + CodeRabbit "no actionable comments" + GitGuardian).

Three small, in-scope additions bundled per ADR-dreaming-022's rationale. (1) A `.env.example` docstring fix — the existing text claimed "nothing in the tree auto-loads `.env`" which is FALSE; the audit confirmed every entrypoint (daydream-cli `cli.py:269`, bench `run_bench.py:251`, pipeline `pipeline.py:632`, plugin CLI `cli.py:164`, hooks_handler `hooks_handler.py:155`) calls `memeval.dotenv_loader.load_root_dotenv()` at startup. Replaced with accurate description of the loader contract, `override=False` semantics, and the `MEMEVAL_DOTENV` explicit-path override. (2) A `llm_call_succeeded` event in `OpenRouterClient.complete()` — symmetric with the 5 existing failure-path emits (`llm_unavailable`, `llm_call_failed`, `llm_retry`, `llm_rate_limited`, `llm_malformed_response`) that already carry `model=self.model`; the success path was previously silent so a healthy daydream call left no trace of which model answered. Now `events.jsonl` carries the model name on healthy runs too — the cleanest signal for verifying `.env`-to-API wiring in production observability. (3) A new regression test `test_dream_model_env_override` (7 tests) guarding the `DREAM_PROVIDER` / `DREAM_MODEL` / explicit-arg-wins / unknown-provider-raises contract on `make_client()`.

### Key state
The daydream subconscious-model surface now has a documented opt-in path: edit `.env`'s `DREAM_MODEL` (or export in shell — shell wins per `override=False`). The model is plumbed via `make_client()` (`llm.py:421-422`) which dispatches on `DREAM_PROVIDER` (default `openrouter`) + `DREAM_MODEL` (default `inclusionai/ling-2.6-flash` per ADR-dreaming-004). The 7-test regression on this dispatch is the structural guard against the "I thought I set DREAM_MODEL but the call still went to the default" failure mode that the model-swap test would otherwise only surface in event traces.

The dreaming event family on the LLMClient side now has six names: the 5 failure paths (`llm_unavailable`, `llm_call_failed`, `llm_retry`, `llm_rate_limited`, `llm_malformed_response`) plus the new `llm_call_succeeded`. All six carry `model=self.model` so consumers can attribute LLM activity by model. This is additive-only: no existing event field removed, no schema break, no consumer migration required.

The `.env.example` doc now accurately reflects the `memeval.dotenv_loader` loader contract — operators reading the file no longer build mental models on the wrong premise that they must `export` or use a `dotenv -f .env --` wrapper.

### Open items
- **PR #116 awaits human review approval.** CodeRabbit's automated walkthrough returned no actionable comments and all CI checks are green; kenhuangus is the suggested human reviewer. A `trig_01XgmEqzFV1VMJnTt4v5HZiH` babysitter cron checks PR state hourly and was wired to push this very KB entry on `reviewDecision == "APPROVED"` — but Scott requested manual `/kb` execution ahead of approval, so this entry lands first. The cron's STEP 0 self-stop check (`git log --grep='PR #116' --grep='ADR-dreaming-022' --all-match`) will detect this commit and self-no-op on every subsequent firing until manually disabled at https://claude.ai/code/routines.
- **Comparative substrate analysis (deepseek vs ling-2.6-flash) deferred to a follow-up PR.** This PR provides the WIRING; it does not run a comparative bench. Once the PR merges and operators opt in via `.env`, the `daydream.chunk_extracted` / `daydream.candidate_rejected` / `chunk_skipped_parse_failed` / `tokens_out` distributions across the two models become observable in `events.jsonl`. After 1–2 weeks of opt-in deepseek use, a successor ADR could supersede ADR-dreaming-004 if comparative data justifies it.
- **CodeRabbit on PR #116 returned ZERO findings.** Either the code is genuinely clean (small surface, all-additive, established patterns reused) or the review profile didn't engage as deeply as on PRs #105/#107/#108 (each of which surfaced 1+ real bug). Worth watching but not actionable.
- **The `llm_call_succeeded` event addition increases event volume** (one emit per successful daydream LLM call; at SWE-Bench-CL plugin-real run scale that's ~10–30 extra events/task). Acceptable; events.jsonl is already gigabytes on full runs and the cardinality is bounded. ADR-dreaming-022 records this tradeoff explicitly.
- **Cross-provider observability parity** — if a future `LocalClient` or `AnthropicClient` ships per ADR-dreaming-006 §Roster, it should also emit `llm_call_succeeded` for cross-provider symmetry. Out of scope for ADR-022.

### Artifacts at time of entry
- [`eval/memeval/dreaming/llm.py`](../eval/memeval/dreaming/llm.py) — `OpenRouterClient.complete()` now emits `llm_call_succeeded` on the success path; `DEFAULT_MODEL` at line 39 unchanged
- [`eval/memeval/dreaming/tests/test_dream_model_env_override.py`](../eval/memeval/dreaming/tests/test_dream_model_env_override.py) — NEW: 7-test regression on `make_client()` env-var dispatch
- [`docs/adrs/ADR-dreaming-022-deepseek-model-swap-test.md`](../docs/adrs/ADR-dreaming-022-deepseek-model-swap-test.md) — NEW: test rationale + bundled changes inline
- [`.env.example`](../.env.example) — docstring fix + `Active test target (ADR-dreaming-022)` comment added above the existing commented `DREAM_MODEL` line
- [`docs/adrs/README.md`](../docs/adrs/README.md) — index row added for ADR-dreaming-022

### Notable since last entry
- **First model-swap test on the daydream side.** All prior dreaming entries (1–9) were on the in-session capture or whole-store consolidation surface; this is the first to exercise an alternative subconscious model. Opt-in path is now established; comparative substrate analysis is the natural follow-up.
- **The `.env.example` docstring was stale by approximately one PR cycle** — `memeval.dotenv_loader` likely shipped in PR #112 (per CodeRabbit's "possibly related PRs" callout on PR #116) and the .env.example doc was never updated. The fix in this PR closes the doc-vs-code mismatch.
- **Observability symmetry as a small but real upgrade.** Production audit paths historically only saw the model name on failure; success was silent. This means any production runs of the daydream function pre-this-PR carry no audit trail of which model successfully extracted memories. Going forward they will. Worth carrying as a default pattern: anywhere a failure path emits a tag, the success path should too — the silence-on-success asymmetry is a common low-signal observability bug.
- **Process pattern that worked here: PR-prep-while-pipeline-runs.** A bench pipeline was running in another terminal; Scott requested a full PR draft built in scratchpad before any repo edit. 8-file package landed under `/private/tmp/.../scratchpad/deepseek-prep/` (README + .env edit + .env.example diff + llm.py diff + regression test + ADR + smoke test + verify commands), reviewed for diff correctness (the first cut of two diffs had broken hunk counts → converted to find/replace format), then applied + verified + committed + pushed in a single move once the pipeline finished. The find/replace edit format proved more robust than unified-diff for AI-prepared patches; worth carrying forward.
- **A babysitter cron was set up to handle the post-approval `/kb` push** (`trig_01XgmEqzFV1VMJnTt4v5HZiH`, hourly), but Scott requested manual `/kb` execution ahead of approval; this entry lands now and the cron's sentinel-detection mechanism handles any future firings without duplicate work.
- **The bigger arc this fits into:** this PR is preparatory infrastructure for the larger prompt-variant research arc captured in the `project_daydream_dream_prompt_paradigms` auto-memory. The research arc surfaced four candidate `EXTRACTION_SYSTEM_PROMPT` variants (V0/V1/V2/V3) plus a critic-driven pivot to substrate-side measurement; the critic flagged that "is what we're measuring an artifact of the model or the prompt?" was unanswerable with only one model data point on the daydream side. This PR adds the second data point.
