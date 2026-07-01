---
id: ADR-harness-017
domain: harness
title: The store scaffolds its own .gitignore — markdown memories are the only git-shareable layer
status: Accepted
date: 2026-07-01
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: production-install hardening 2026-07-01 (what should a plugin user commit?)
---

# ADR-harness-017: The store scaffolds its own `.gitignore` — markdown memories are the only git-shareable layer

**Status:** Accepted · **Date:** 2026-07-01 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The store directory (`.cookbook-memory/` in the user's project) mixes two kinds of
content:

- **Markdown memories** (`markdown/<type>/mem_*.md`) — human-readable, one file per
  memory, mergeable in git. This is the layer a team can share: commit, review,
  merge.
- **Everything else** — SQLite/FTS5 and graph databases (plus their WAL/SHM
  companions), lock files/dirs (`.dream.lock`, `markdown/.okf.lock`), the append-only
  `events.jsonl`, generation markers, and per-session dream sidecar state. These are
  per-machine binary/append-only artifacts: two clones cannot merge a SQLite file,
  and locks/events are meaningless outside the machine that wrote them.

Without guidance, a plugin user's `git status` fills with unmergeable noise, and the
natural `git add .` commits databases that will conflict on the first concurrent
branch. The eval repo itself hit exactly this (tracked `fts5.db` / `graph.db` /
`events.jsonl` churning on every session).

## Options considered
- **The store writes its own `.gitignore` at creation** (chosen): the first writer
  into a fresh store drops a 4-rule `.gitignore` (`*`, `!*/`, `!.gitignore`,
  `!markdown/**/*.md`). Zero user action, correct by default, and the policy ships
  with the artifact it governs. Never overwrites an existing file, so user edits win.
- **Document "add these lines to your .gitignore"**: README-only guidance. Rejected
  as the primary mechanism — it fails exactly for the users who don't read the
  storage internals; kept as documentation of what the scaffold does.
- **Ignore the whole store** (`.cookbook-memory/` in the project `.gitignore`):
  simplest, but throws away the shareable layer — team-shared memory through git is
  a real use of the markdown backend. Rejected.
- **Commit everything, ship a merge driver for the databases**: no sane merge
  semantics exist for SQLite/graph blobs. Rejected.

## Decision
`ensure_store_gitignore()` in the plugin core writes `<store>/.gitignore` when the
store directory is born; the events stream (the first writer into every fresh store —
each hook and recall emits an event) invokes it on write. Properties:

- **Allowlist shape**: ignore `*`, re-include directories (`!*/`) so git traverses,
  re-include the scaffold itself and `markdown/**/*.md`. Everything future backends
  drop into the store is ignored by default — new artifact types need no rule update.
- **Write-once**: never overwrites an existing `.gitignore`; the user's policy wins.
- **Fail-open** (ADR-harness-006): scaffolding failure never raises into a session.

The eval repo's own dogfood store follows the same policy: its transient tracked
files (`events.jsonl`, `fts5.db`, `graph.db`, `memory.db`, dream sidecar JSON,
`.okf-generation`) are untracked; the markdown memories stay committed.

## Rationale
The store is the plugin's on-disk contract with the user's repo; the plugin — not
the user — knows which files are portable. An allowlist written by the store itself
travels with every store (any project, any harness), needs no per-repo setup, and
makes the *safe* action (`git add .cookbook-memory`) also the *correct* action, as
verified: `git add -A` on a populated store stages exactly the `.gitignore` and the
markdown memories.

## Tradeoffs & risks
- **Fresh clones have markdown-only recall**: the derived indexes (FTS5, graph,
  vectors) are not committed and there is currently no bulk rebuild-from-markdown;
  the markdown backend syncs from files, and the other backends repopulate as new
  memories are written. Accepted for now — a `memory-cli reindex` is the natural
  follow-up if cold-clone retrieval quality matters.
- **Archived result stores change shape**: eval runs that copy a live store into
  `results/**/_memory/` now carry the `.gitignore`, so committing archived stores
  skips their databases unless force-added (`git add -f`). Flagged to the eval
  workstream; provenance-critical runs can force-add.
- **Untracking already-tracked files is a one-time pull disturbance**: teammates
  with local changes to the previously-tracked databases will see modify/delete
  conflicts once; resolve by keeping the local (now-ignored) copy.
- The blanket `*` also ignores any future *intentionally*-shareable non-markdown
  artifact — such a backend must update the scaffold (and existing stores keep
  their old file, by design).

## Consequences for the build
- **Policy:** the scaffold content lives in one place
  (`cookbook_memory.core.config.STORE_GITIGNORE`); tests pin the four load-bearing
  rules and the write-once/fail-open behavior.
- **Policy:** repo-level `.gitignore` rules for store internals (e.g. `*.db-wal`)
  remain as belt-and-braces but are no longer the mechanism.
- README install docs state the commit policy: commit `markdown/**/*.md`, nothing
  else from the store.
