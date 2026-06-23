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
