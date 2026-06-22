# Architecture Decision Records (ADRs)

This directory holds the project's **Architecture Decision Records** — one file per
load-bearing technical decision, with the full reasoning (context, options weighed,
the choice, rationale, tradeoffs accepted, consequences for the build).

> **Why ADRs?** A list of choices is worthless; a list of *justified* choices is an
> architecture. Each ADR records the **WHY** so any collaborator can defend a
> decision — or revisit it later knowing exactly what it cost and what it assumed.
> Format follows the standard ADR approach: context · options · decision · rationale ·
> tradeoffs · consequences.

The ADRs are the **durable record of why**. The human-readable architecture
overview lives in [`../../architecture.md`](../../architecture.md) (the *how & where*
contract); the ADRs are the *why behind* it and should not churn. When an ADR's
decision changes, write a **new** ADR that supersedes the old one — never edit an
accepted ADR's reasoning in place.

## Naming convention — by workstream domain

Files are named **`ADR-<domain>-NNN-<slug>.md`**, with **per-domain** sequential
numbering. The four domains map to the four parallel workstreams (see
[`../../plan.md`](../../plan.md) / [`.github/CODEOWNERS`](../../.github/CODEOWNERS)):

| Domain | Owner | Covers |
|---|---|---|
| **harness** | Keith (P1) | the Claude Code plugin (MCP · hooks · skills), log adapter, Daydreamer wiring, events stream, fail-open policy |
| **storage** | Brent (P3) | the Orchestrator / stores / router — the persistence + retrieval seam |
| **dreaming** | Scott B. (P4) | the two isolated subconscious functions — Daydreaming (in-session capture) and Dreaming (whole-store consolidation) — and the shared subconscious model |
| **eval** | Ken (P2) | the eval ↔ memory black-box boundary, package extraction, benchmark protocol |

Pick the domain by **which workstream owns the decision**, not where the code
happens to sit today. A cross-cutting decision is filed under the domain whose
*boundary* it primarily governs (e.g. package extraction is filed under `eval`
because it exists to enforce the eval black-box boundary, even though Keith executes
the move).

## Decision index

| ADR | Decision | Status | Contract |
|-----|----------|--------|----------|
| [ADR-eval-001](ADR-eval-001-extract-memory-package.md) | Extract the memory system into its own package; `memeval` stays pure eval | Accepted | no |
| [ADR-eval-002](ADR-eval-002-docker-free-code-grading.md) | Docker-free CODE grading: agentic Claude Code loop + `LocalExecGrader`; SWE-bench Docker grader removed | Accepted | no |
| [ADR-storage-001](ADR-storage-001-orchestrator-in-process-library.md) | Orchestrator is an in-process library; store-by-`$MEMORY_STORE`, no daemon | Accepted | **yes** |
| [ADR-harness-001](ADR-harness-001-claude-code-plugin-shape.md) | Claude Code plugin = bundled MCP server + hooks + skills | Accepted | no |
| [ADR-harness-002](ADR-harness-002-recall-remember-mcp-tools.md) | `recall`/`remember` are MCP tools that call the Orchestrator | Superseded by [ADR-harness-008](ADR-harness-008-recall-only-conscious-surface.md) | **yes** |
| [ADR-dreaming-001](ADR-dreaming-001-daydreaming-stop-fired.md) | Daydreaming = in-session capture, auto `Stop`/`PreCompact`-fired (day scope) — its own entrypoint | Accepted | **yes** |
| [ADR-dreaming-002](ADR-dreaming-002-dreaming-consolidation-cli.md) | Dreaming = whole-store consolidation via `memory dream --all` CLI (night scope) — its own entrypoint | Accepted | **yes** |
| [ADR-dreaming-003](ADR-dreaming-003-consolidation-llmclient.md) | Subconscious model = swappable `LLMClient`, OpenRouter-first (shared helper) | Superseded by [ADR-dreaming-006](ADR-dreaming-006-llmclient-completion-dataclass.md) | **yes** |
| [ADR-dreaming-004](ADR-dreaming-004-default-subconscious-model.md) | Default subconscious model = `inclusionai/ling-2.6-flash` via OpenRouter | Accepted | no |
| [ADR-dreaming-005](ADR-dreaming-005-v1-inline-redaction.md) | v1 Daydream inlines log reading + secret redaction (Claude-only, `detect-secrets` structured detectors + custom plugins) | Accepted | **yes** |
| [ADR-dreaming-006](ADR-dreaming-006-llmclient-completion-dataclass.md) | `LLMClient.complete()` returns a `Completion` dataclass with token counts (replaces ADR-003's signature) | Accepted | **yes** |
| [ADR-dreaming-007](ADR-dreaming-007-stop-hook-driven-turn-cursor.md) | Daydream turn = user prompt + Claude response(s) + Stop hook; cursor → EOF per invocation (v1, Stop-hook-only) | Accepted | no |
| [ADR-dreaming-008](ADR-dreaming-008-memory-cli-console-script.md) | `memory` CLI is a standalone console script in `eval/memeval/dreaming/cli.py` | Superseded by [ADR-dreaming-016](ADR-dreaming-016-rename-memory-cli-to-daydream-cli.md) | **yes** |
| [ADR-dreaming-009](ADR-dreaming-009-events-shim.md) | Daydream events shim = no-op + local `daydream-events.jsonl` diary until ADR-harness-007 ships | Accepted | no |
| [ADR-dreaming-010](ADR-dreaming-010-redactedtext-newtype.md) | `RedactedText` NewType structurally enforces the redaction trust boundary (mypy-checked, updates ADR-006's `complete()` signature) | Accepted | **yes** |
| [ADR-dreaming-011](ADR-dreaming-011-expanded-redaction-scope.md) | Expanded redaction scope — DB/URL-credential detectors + explicit out-of-scope policy + FP/FN audit file (amends ADR-005) | Accepted | **yes** |
| [ADR-dreaming-012](ADR-dreaming-012-openrouter-missing-key-failopen.md) | `OpenRouterClient` missing-API-key = no-op `Completion` + `llm_unavailable` event + no cursor advance | Accepted | **yes** |
| [ADR-dreaming-013](ADR-dreaming-013-cursor-advance-ordering.md) | Cursor-advance ordering — memories-then-cursor, atomic sidecar write, no advance on exception | Accepted | **yes** |
| [ADR-dreaming-014](ADR-dreaming-014-concurrent-daydream-flock.md) | Concurrent Daydream invocations — `flock` per `session_id` + idempotent exit-0 | Accepted | **yes** |
| [ADR-dreaming-015](ADR-dreaming-015-filesystem-state-management.md) | Per-session filesystem state — Python `<basedir>` resolution + uniform 30-day retention TTL + throttled sweeper-on-invocation | Superseded by [ADR-dreaming-019](ADR-dreaming-019-memory-store-is-a-directory.md) (§1 only — §2/§3/§4 stand) | **yes** |
| [ADR-dreaming-016](ADR-dreaming-016-rename-memory-cli-to-daydream-cli.md) | Console script renamed `memory` → `daydream-cli` to eliminate PATH-collision risk (supersedes ADR-008) | Superseded by [ADR-dreaming-018](ADR-dreaming-018-cli-argparse-exit-code.md) (exit-code policy only — rename remains active) | **yes** |
| [ADR-dreaming-017](ADR-dreaming-017-precompact-concurrency-and-transcript-trust.md) | PR5 plugin-shim operational contract — PreCompact silent-skip on Stop concurrency + transcript-path trust model (extends ADR-001) | Accepted | **yes** |
| [ADR-dreaming-018](ADR-dreaming-018-cli-argparse-exit-code.md) | CLI argparse-error exit code is `1` (not `2`) — Claude Code reserves exit 2 for hook-blocking (partially supersedes ADR-016) | Accepted | **yes** |
| [ADR-dreaming-019](ADR-dreaming-019-memory-store-is-a-directory.md) | `$MEMORY_STORE` is a directory (not a file-sentinel) — auto-mkdir; ValueError on file (partially supersedes ADR-015 §1 only) | Accepted | **yes** |
| [ADR-harness-003](ADR-harness-003-log-extraction-chunking.md) | `dream` log-extraction chunking = one turn = one chunk + prior-summary overlap | Accepted | no |
| [ADR-harness-004](ADR-harness-004-dream-state-sidecar.md) | `dream` state = on-disk JSON sidecar (cursor + last_summary + recent_memory_ids) | Accepted | no |
| [ADR-harness-005](ADR-harness-005-log-adapter-redaction.md) | The log adapter redacts secrets before any model call | Accepted | no |
| [ADR-harness-006](ADR-harness-006-fail-open.md) | Every hook/tool is fail-open — never break the user's session | Accepted | no |
| [ADR-harness-007](ADR-harness-007-memory-events-stream.md) | Structured memory-events stream, observability-platform-bound (Langfuse) | Accepted | **yes** |
| [ADR-harness-008](ADR-harness-008-recall-only-conscious-surface.md) | The conscious surface is recall-only; writes happen via the Daydreamer | Accepted | **yes** |
| [ADR-harness-009](ADR-harness-009-client-agnostic-skills.md) | One canonical skill, materialized into each harness's native bundle by a build step (single native install) | Accepted | no |

> **Provenance.** These eleven ADRs were extracted from the consolidated ADR-P1…P11
> series in
> [`../harnesses/05-plugin-mvp-plan.md`](../harnesses/05-plugin-mvp-plan.md) (the
> Claude Code plugin MVP build plan), which remains the narrative companion. The
> `origin:` front-matter field on each ADR records its original ADR-P id.

## The schema (front-matter + sections)

Every ADR has YAML front-matter followed by the ADR body:

```markdown
---
id: ADR-<domain>-NNN
domain: harness | storage | dreaming | eval
title: <the decision, stated as a choice>
status: Accepted | Proposed | Superseded
date: YYYY-MM-DD
contract: true | false          # does this establish a shared cross-cutting contract?
supersedes: <ADR id or none>
superseded_by: <ADR id or none>
owner: <person (Pn)>
origin: <source doc / original id, if extracted>
---

# ADR-<domain>-NNN: <the decision, stated as a choice>

**Status:** … · **Date:** … · **Contract:** …
**Supersedes:** … · **Superseded by:** …

## Context
What forced this decision; what's true that makes it a real choice, not a default.

## Options considered
The genuine alternatives, each with the tradeoff that matters here. No strawmen.

## Decision
What was chosen.

## Rationale
Why this option wins *here*. The sentence you'd repeat to defend it.

## Tradeoffs & risks
What we gave up, what could go wrong, how we'd mitigate or when we'd revisit.
Naming the cost is non-negotiable — it's the proof the choice was actually made.

## Consequences for the build
- **Policy consequences** — a rule every dependent must follow.
- **Contract consequences** (only if `contract: true`) — the **source of truth**
  (file the shape lives in), the **shape** (the minimum-viable type/signature), and
  the **exhaustive consumers** (the code that must handle every case and stay in
  sync). A contract ADR that only says "we use an interface" is too thin.
```

## When & how to write an ADR

Write an ADR when a decision is **load-bearing and not obvious** — i.e. a future
collaborator (or CTO) would reasonably ask *"why did you do it that way and not the
obvious alternative?"* and the answer isn't already self-evident from the code.

- **Do write one for:** a choice between real alternatives (a store backend, a
  trigger mechanism, a wire/tool schema, an isolation model, a trust boundary, a
  fail-open vs fail-closed policy); anything that **establishes a contract** other
  workstreams build against (set `contract: true` and fill the contract
  consequences); a decision that **reverses** an earlier one (supersede, don't edit).
- **Don't write one for:** reversible implementation details, naming, or anything
  fully determined by an already-accepted ADR or the frozen contract.

**How:**

1. **Number within your domain.** Find the highest `ADR-<domain>-NNN` and use the
   next integer. Numbering is per-domain; never re-number, never reuse a number.
2. **Copy the schema above.** Fill every section — an empty "Tradeoffs & risks" means
   the decision wasn't really made.
3. **Set `contract:` honestly.** If multiple workstreams will build against the
   shape, it's a contract: name the source of truth, the shape, and the exhaustive
   consumers so the build can freeze a signature against it.
4. **Add a row to the decision index** in this README (keep it ordered to match the
   files).
5. **Cross-link** related ADRs with relative links (`[ADR-storage-001](…)`), and
   reference the PRD/architecture requirement the decision serves.
6. **Land it via PR** like any change (per [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md)),
   editing only your domain's ADRs. A decision that touches the **frozen contract**
   (`schema.py`/`protocols.py` + `architecture.md`) still follows the `[CONTRACT]`
   PR process — the ADR is the *why*, the contract edit is the *what*.
7. **Superseding a decision:** write a new ADR with `supersedes: ADR-<domain>-NNN`,
   set the old one's `status: Superseded` and `superseded_by:` — but **never delete
   or rewrite the old ADR's body**. Its reasoning is the historical record.
