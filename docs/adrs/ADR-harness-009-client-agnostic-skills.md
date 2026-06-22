---
id: ADR-harness-009
domain: harness
title: Skills are canonical Agent-Skills folders, placed per-harness by an install command
status: Accepted
date: 2026-06-21
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: design session 2026-06-21 (cross-harness skills research)
---

# ADR-harness-009: Skills are canonical Agent-Skills folders, placed per-harness by an install command

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The plugin ships a `recall` skill (a `SKILL.md` describing how to use the recall
tool). The question was where it lives and how it stays client-agnostic across the
three target harnesses without duplicating it or coupling it to Claude Code.

Research into the harnesses settled it: skills are **one open standard** — [Agent
Skills](https://agentskills.io) (originally Anthropic, now adopted across ~40 tools
including all three targets). A skill is a folder with a `SKILL.md` (name +
description + instructions). The *same folder* is valid in every harness; only the
**discovery path** each scans differs:

| Harness | Skill discovery paths (relevant) |
|---|---|
| Claude Code | `.claude/skills/<name>/`, `~/.claude/skills/`, `<plugin>/skills/<name>/` |
| OpenCode | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` (and `~` equivalents) |
| Codex | `.agents/skills/` (CWD/repo), `~/.agents/skills/`, `/etc/codex/skills` |

There is no config field in any of them to point at an arbitrary skills directory,
and no reliable symlink/external-reference mechanism. A committed symlink from the
Claude bundle into a shared dir was tried and rejected — it breaks git
(symlink-vs-directory ambiguity) and does not survive packaging or Windows.

## Options considered
- **Canonical skill in the core package + a per-harness install command** (chosen):
  the skill lives once as a standard Agent-Skills folder in the package; an adapter
  `install` command places (copies/links) it into the target harness's discovery path
  on the user's machine at install time.
- Skill physically in the Claude bundle (`adapters/claude_code/skills/`): works for
  Claude only; duplicating per adapter re-introduces N copies in the repo.
- Committed symlink from the bundle to a shared dir: rejected — breaks git/packaging.
- Build-time copy into each bundle: machinery that fights the spec and still leaves
  the live source-dir install (`/plugin marketplace add`) and the non-Claude harnesses
  unsolved.

## Decision
**The canonical skill is a single Agent-Skills folder in the core package**
(`cookbook_memory/skills/<name>/SKILL.md`) — the portable, standard, client-agnostic
artifact. **Each harness adapter places it into that harness's discovery path via an
install command** (`memory-cli install --harness claude|codex|opencode`), which copies
or links the canonical skill into `.claude/skills/` / `.agents/skills/` /
`.opencode/skills/` (project or user scope). The adapter's only skills responsibility
is install-time *placement*; it owns no skill content.

## Rationale
This honors "generic core, thin adapters": the skill *content* is a standard artifact
in the core (write once), and each adapter does the one harness-specific thing it must
— put the standard folder where that harness looks. It uses the Agent-Skills standard
as intended (build once, run across skills-compatible agents) instead of fighting it
with symlinks or copies committed to git. `.agents/skills/` notably serves both Codex
and OpenCode, so one install target can cover two harnesses.

## Tradeoffs & risks
The skill must be *installed* (a step) rather than auto-present in a checked-out bundle
— a one-time `memory-cli install` per harness. Accepted: it's the same model as any
skills-based tool, and it's the honest place for the per-harness difference. Copy vs.
symlink at install time is the install command's choice (copy is safest cross-platform;
a `--link` option can avoid drift for local dev).

## Consequences for the build

- **Policy:** the canonical skill lives at `cookbook_memory/skills/<name>/SKILL.md` and
  is shipped as core package data. No skill files or symlinks under
  `adapters/claude_code/`.
- **Policy:** each adapter provides install-time placement into its harness's discovery
  path; `memory-cli install --harness <h> [--scope project|user] [--link]` is the
  entry point. `.agents/skills/` covers Codex + OpenCode; `.claude/skills/` (or the CC
  plugin bundle) covers Claude Code.
- **Policy:** skill content is harness-agnostic Agent-Skills markdown — it must not
  reference a specific harness's tool-id namespace.
