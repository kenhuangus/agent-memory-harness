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
