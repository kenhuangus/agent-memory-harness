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
