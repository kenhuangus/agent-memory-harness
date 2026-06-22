# Knowledge Base — harness

**Domain owner:** Keith
**First entry:** 2026-06-22

Append-only journal of project-story snapshots for the **harness** workstream.
See [README.md](README.md) for conventions.

---

## 2026-06-22T11:32 — entry 1

**Triggered by:** Initial KB seeding via cross-cutting `/kb all` run — establishes baseline state of the harness workstream as the `.kb/` convention lands in the repo.
**Branch:** harness/add-kb-command
**Related ADRs:** ADR-harness-001 through ADR-harness-009
**Cross-domain run:** [KB-storage.md](KB-storage.md), [KB-dreaming.md](KB-dreaming.md), [KB-eval.md](KB-eval.md)

### Summary
The harness workstream owns the model-agnostic memory layer the rest of the project is built around — the Claude Code plugin (`plugin/cookbook_memory/`), the MCP/hooks/skills surface, the log adapter, and the integration seam that lets a coding agent recall persisted memory and append new ones without knowing the storage backend. As of this entry, the walking skeleton (PR #22) has landed, the recall-only conscious surface decision (ADR-harness-008) has been implemented as a `MemoryClient` with client-agnostic skills materialized per-adapter at build time (ADR-harness-009, PR #51), and the v1 functional loop is closed end-to-end via the Stop-hook plugin shim that fires the daydream-cli on session-end (PR #48, joint with dreaming).

### Key state
The conscious surface is **recall-only** (ADR-harness-008) — `recall` is exposed as an MCP tool, but writes happen out-of-band through the Daydreamer, not through an MCP `remember` call. ADR-harness-002 (the original recall/remember pair) is superseded by 008. Skills are **client-agnostic** with one canonical source under `plugin/cookbook_memory/skills/` and per-adapter native bundles materialized by a build step (ADR-harness-009), so Claude Code, OpenCode, and any future adapter install from the same canonical skill. Every hook and tool is **fail-open** (ADR-harness-006) — the memory layer is never allowed to break the user's session. The MCP startup race that gave 40% first-try recall was closed in PR #30 with a priming turn; first-try recall is now 100%. The plugin ships as a build artifact (`plugin/cookbook_memory/adapters/claude_code/`) including `.mcp.json`, hooks_handler, and the recall skill bundle.

### Open items
- The OpenCode adapter is not yet implemented — the `plugin/cookbook_memory/adapters/` directory currently materializes only the Claude Code adapter. The canonical-skill build mechanism (ADR-harness-009) was designed so OpenCode plugs in without changing the skill source, but no `adapters/opencode/` exists yet.
- The structured memory-events stream (ADR-harness-007, Langfuse-bound) is accepted in principle; the events shim currently lives under dreaming (`eval/memeval/dreaming/events.py`) as a no-op + local JSONL diary per ADR-dreaming-009 until the harness-bound observability layer ships.
- The PreCompact hook concurrency contract with Stop (ADR-dreaming-017) is implemented but cross-validation across the two hooks is still informal — no automated test confirms they don't race in a real Claude Code session under load.

### Artifacts at time of entry
- [`architecture.md`](../architecture.md)
- [`prd.md`](../prd.md)
- [`plan.md`](../plan.md)
- [`plugin/README.md`](../plugin/README.md)
- [`plugin/docs/walking-skeleton.md`](../plugin/docs/walking-skeleton.md)
- [`plugin/cookbook_memory/`](../plugin/) — the plugin package source
- [`docs/adrs/ADR-harness-001-claude-code-plugin-shape.md`](../docs/adrs/ADR-harness-001-claude-code-plugin-shape.md)
- [`docs/adrs/ADR-harness-002-recall-remember-mcp-tools.md`](../docs/adrs/ADR-harness-002-recall-remember-mcp-tools.md) (superseded by 008)
- [`docs/adrs/ADR-harness-003-log-extraction-chunking.md`](../docs/adrs/ADR-harness-003-log-extraction-chunking.md)
- [`docs/adrs/ADR-harness-004-dream-state-sidecar.md`](../docs/adrs/ADR-harness-004-dream-state-sidecar.md)
- [`docs/adrs/ADR-harness-005-log-adapter-redaction.md`](../docs/adrs/ADR-harness-005-log-adapter-redaction.md)
- [`docs/adrs/ADR-harness-006-fail-open.md`](../docs/adrs/ADR-harness-006-fail-open.md)
- [`docs/adrs/ADR-harness-007-memory-events-stream.md`](../docs/adrs/ADR-harness-007-memory-events-stream.md)
- [`docs/adrs/ADR-harness-008-recall-only-conscious-surface.md`](../docs/adrs/ADR-harness-008-recall-only-conscious-surface.md)
- [`docs/adrs/ADR-harness-009-client-agnostic-skills.md`](../docs/adrs/ADR-harness-009-client-agnostic-skills.md)

---

## 2026-06-22T12:45 — entry 2

**Triggered by:** Milestone: production install landed (PR #70 / ADR-harness-010) + a live install-readiness verification of the plugin from `main`.
**Branch:** main
**Related ADRs:** ADR-harness-010 (new), ADR-harness-009 (one clause superseded)

### Summary
The harness crossed from "builds an artifact" to "installs from git on a clean machine." ADR-harness-010 (PR #70) reverses the one git-ignored-bundle clause of ADR-harness-009: the materialized Claude Code bundle is now **committed** at `plugin/marketplace/cookbook-memory/` and a **root `.claude-plugin/marketplace.json`** points at it via a `git-subdir` source, so `claude plugin marketplace add <repo>` + `claude plugin install cookbook-memory` delivers the recall skill, the MCP recall tool, and the five lifecycle hooks together with no repo clone. The package half of the install is a `pip install --user "cookbook-memory[mcp] @ git+…#subdirectory=plugin"`, which also pulls the frozen-contract dep (`agent-memory-eval`) as a `git+URL`. A clause of ADR-harness-009 is superseded; its canonical-skill authoring model still holds. The `/kb` command + `.kb/` journal also landed this window (PR #71) but is process tooling, not harness runtime.

### Key state
The bundle invokes its surfaces **by module, not by console script** — `python3 -m cookbook_memory mcp` for MCP and `python3 -m cookbook_memory.adapters.claude_code.hooks_handler <Event>` for hooks — so resolution depends only on `cookbook_memory` being importable by the `python3` Claude Code runs. This is why the README mandates `pip install --user`, not pipx: a pipx-isolated install is invisible to that interpreter. The committed bundle and the canonical adapter source (`adapters/claude_code/`) are in sync (both module-form); the only divergence is the **git-ignored, untracked `plugin/dist/claude-code/`** artifact, which still carries the old `memory-cli`/`memory-hook` console-script wiring — harmless because it's not the install target. Everything remains fail-open (ADR-harness-006) and recall-only (ADR-harness-008). A live check this session confirmed: package imports, hooks_handler exits 0, the module CLI dispatches, and a remember→query recall round-trip retrieves the stored fact (BM25, lexical-default — a no-term-overlap query correctly returns empty, which is backend behavior, not a break).

### Open items
- Stale `plugin/dist/claude-code/` bundle carries outdated `memory-cli`/`memory-hook` commands; gitignored and not an install target, but worth deleting to avoid confusion.
- The two documented install steps are the only thing between `main` and a working local install — nothing is installed against the **system** `python3` yet (verified absent this session); the repo `.venv` is what currently has it.
- Recall is lexical-default (BM25): queries with no shared terms return empty by design — same edge to keep in mind once a vector/embedding backend becomes the routing target.
- OpenCode adapter still not implemented (carried from entry 1).
- PreCompact/Stop concurrency contract still lacks an automated under-load race test (carried from entry 1).

### Artifacts at time of entry
- [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) — root git-subdir install manifest
- [`plugin/marketplace/cookbook-memory/`](../plugin/marketplace/cookbook-memory/) — the committed, installable bundle
- [`plugin/cookbook_memory/adapters/claude_code/`](../plugin/cookbook_memory/adapters/claude_code/) — canonical adapter source (manifests + hooks + build.py)
- [`plugin/README.md`](../plugin/README.md) — the two-step install instructions
- [`plugin/pyproject.toml`](../plugin/pyproject.toml) — `[mcp]` extra, `git+URL` contract dep, console-script conveniences
- [`docs/adrs/ADR-harness-010-commit-release-bundle.md`](../docs/adrs/ADR-harness-010-commit-release-bundle.md)
- [`docs/adrs/ADR-harness-009-client-agnostic-skills.md`](../docs/adrs/ADR-harness-009-client-agnostic-skills.md) (one clause superseded by 010)

### Notable since last entry
- ADR-harness-010 landed (PR #70) — release bundle is now committed + installs from git with no clone; reverses ADR-harness-009's git-ignored-bundle clause.
- Install is now two real steps (`pip install --user` of the package, then `claude plugin install`); both verified resolvable in this session.
- `/kb` command + `.kb/` per-domain journal added (PR #71) — process tooling.
- Confirmed a stale, untracked `plugin/dist/` bundle with old console-script wiring exists alongside the live module-form bundle.
