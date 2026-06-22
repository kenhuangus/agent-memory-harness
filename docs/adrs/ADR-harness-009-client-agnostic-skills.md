---
id: ADR-harness-009
domain: harness
title: One canonical skill, materialized into each harness's native bundle by a build step
status: Accepted (one clause superseded by ADR-harness-010)
date: 2026-06-21
contract: false
supersedes: none
superseded_by: ADR-harness-010 (the "bundle is git-ignored, never committed" clause only)
owner: Keith (P1)
origin: design session 2026-06-21 (cross-harness skills research)
---

# ADR-harness-009: One canonical skill, materialized into each harness's native bundle by a build step

**Status:** Accepted — except the "bundle is git-ignored, never committed" clause,
**superseded by [ADR-harness-010](ADR-harness-010-commit-release-bundle.md)** (the
shipping bundle is committed at a tracked path so the plugin installs from git). The
authoring model below — one canonical skill, build-time materialization, single native
install — still stands. · **Date:** 2026-06-21 · **Contract:** no
**Supersedes:** none · **Superseded by:** ADR-harness-010 (one clause)

## Context
The plugin ships a `recall` skill (a `SKILL.md` describing how to use the recall
tool). The question is where it lives and how it reaches each target harness while
satisfying three hard requirements:

1. **No duplication** — the skill content (and any shared markdown/code) exists
   exactly once in the repo; it is never copied or symlinked per-harness *in git*.
2. **Shared foundations** — the harness-agnostic core is the single source; adapters
   stay thin.
3. **Single, native, per-harness install** — the end user runs *one* command that is
   that harness's own standard install, and gets the whole capability. No extra,
   tool-specific "now also install the skill" step.

Skills are **one open standard** — [Agent Skills](https://agentskills.io)
(originally Anthropic, now adopted across ~40 tools including all three targets). A
skill is a folder with a `SKILL.md` (name + description + instructions). The *same
folder* is valid in every harness; only the **discovery path** each scans differs,
and — critically — each harness will load skills bundled *inside its own native
package*:

| Harness | Skill discovery (incl. native bundle) |
|---|---|
| Claude Code | `<plugin>/skills/<name>/` (inside an installed plugin), plus `.claude/skills/`, `~/.claude/skills/` |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` (and `~` equivalents) |
| Codex | `.agents/skills/` (CWD/repo), `~/.agents/skills/`, `/etc/codex/skills` |

The earlier framing treated bundling as impossible because the only two ways
considered were a **committed symlink** (breaks git's symlink-vs-dir handling, does
not survive packaging or Windows) or a **committed copy** (N duplicates in the repo
— violates requirement 1). That was a false dichotomy: it conflated *duplication in
the repo* with *materialization in the build artifact*. A producer-side build step
can copy the single canonical skill into each harness's bundle at package/release
time, leaving the repo with one source and no committed symlink — and the user then
installs that bundle with one native command.

Verified empirically (2026-06-21): a Claude Code plugin whose `<plugin-root>/skills/
recall/SKILL.md` is a **materialized real file** installs via the single native
`claude plugin install` flow and `claude plugin details` then reports
`Skills (1) recall` alongside `Hooks (5)` and `MCP servers (1)` — one install, all
three components, no symlink.

## Options considered
- **One canonical skill + a build step that materializes it into each harness's
  native bundle** (chosen): the skill lives once in the core; the release/build for
  each adapter copies it into that adapter's package (`adapters/claude_code/skills/`
  for the CC plugin, the equivalent for Codex/OpenCode). The user runs only the
  harness-native install (`claude plugin install …`, or the Codex/OpenCode
  equivalent) and gets skill + tools + hooks together.
- **Per-harness `install` command the user must run separately** (the prior
  direction): canonical skill in the core, but placed onto the user's machine by a
  bespoke `memory-cli install --harness <h>` *after* the native plugin install.
  Rejected: it violates requirement 3 — the user faces a non-standard second step,
  and the native Claude plugin install reports `Skills (0)` on its own. It also
  splits install state (the CC marketplace install honors `CLAUDE_CONFIG_DIR`; a
  home-dir skill copy does not), so the two halves can land in different places.
- **Committed copy of the skill in each bundle**: one native install, but N
  duplicates in git — violates requirement 1.
- **Committed symlink from each bundle to the canonical dir**: avoids duplicate
  *content* but breaks git/packaging/Windows — not portable.

## Decision
**The canonical skill is a single Agent-Skills folder in the core package**
(`cookbook_memory/skills/<name>/SKILL.md`) — the one source of truth, shipped as
package data. **A build/release step materializes (copies) that canonical skill into
each harness adapter's native bundle**, so each adapter ships a self-contained
package containing its manifest + MCP + hooks **+ the skill**. **The end user
installs with one native, per-harness command** and receives the whole capability:

- Claude Code: `claude plugin install cookbook-memory` (or `/plugin install`) →
  skill + MCP + hooks.
- Codex / OpenCode: the harness's own native skills/extension install, fed from the
  same materialized bundle (`.agents/skills/` serves both).

The materialized copies under `adapters/*/skills/` are **build outputs, not committed
source**: generated from the canonical folder and git-ignored, so the repo holds the
skill exactly once.

## Rationale
This is the only option that satisfies all three requirements at once: one source in
the repo (no duplication), a thin adapter whose skill content is *generated* not
authored, and a single native install for the user. It uses the Agent-Skills standard
as intended — author once, run across skills-compatible agents — and puts the one
unavoidable per-harness difference (where the bundle's skills live) in the *build*,
where it belongs, instead of on the user's hands at install time. The packaging
objection that sank bundling before was specific to *committed* symlinks/copies;
build-time materialization sidesteps it entirely.

## Tradeoffs & risks
A build/release step is now load-bearing: the bundles must be (re)materialized
whenever the canonical skill changes, or a stale/missing skill ships. Mitigation: the
materialization is a single deterministic copy from one source (cheap, scriptable,
testable), it runs in the package/release pipeline, and the eval rebuilds the bundle
before each run so a checkout-and-run developer is never testing a stale copy. The
generated `adapters/*/skills/` dirs must be git-ignored so a built tree never commits
duplicates; a stray commit of them would reintroduce the duplication this ADR forbids
— enforced by `.gitignore` and a CI check. For pure local dev, the build step may
symlink instead of copy (drift-free), but release artifacts always copy (portable).

## Consequences for the build

- **Policy:** the canonical skill lives at `cookbook_memory/skills/<name>/SKILL.md`
  and is shipped as core package data. It is authored exactly once; no other
  `SKILL.md` is committed.
- **Policy:** a build step materializes the canonical skill into each harness
  adapter's bundle (`adapters/<harness>/skills/<name>/`) from that single source. The
  materialized dirs are **git-ignored build outputs**, never committed.
- **Policy:** the end user's install is the harness's own native command and nothing
  else — for Claude Code, `claude plugin install` delivers skill + MCP + hooks in one
  step (verified: `plugin details` → `Skills (1)`). No separate skill-placement step
  is required of the user.
- **Policy:** skill content is harness-agnostic Agent-Skills markdown — it must not
  reference a specific harness's tool-id namespace.
- **Policy:** the eval harness builds (materializes) the bundle before a run and
  installs it via the native flow, so the benchmark exercises exactly what a user
  installs.
