# Project Knowledge Base

This directory holds the team's project-story journal — one append-only file per
workstream domain. Entries are timestamped checkpoints of project state, written
to capture context that doesn't belong in code, ADRs, or PRs but is worth
preserving across the sprint.

The four domains mirror [`../docs/adrs/README.md`](../docs/adrs/README.md):

| Domain | Owner | File |
|---|---|---|
| **harness**  | Keith    | [KB-harness.md](KB-harness.md)   |
| **storage**  | Brent    | [KB-storage.md](KB-storage.md)   |
| **dreaming** | Scott    | [KB-dreaming.md](KB-dreaming.md) |
| **eval**     | Ken      | [KB-eval.md](KB-eval.md)         |

## Conventions

- **Append-only.** Each entry is a snapshot at a moment in time; later entries
  supersede earlier ones where they conflict. Never edit a prior entry in place.
- **No secrets, no PII.** This directory is committed to the repo and visible to
  every collaborator. Treat KB content with the same privacy discipline as any
  other tracked artifact — no API keys, no production data, no personal info
  beyond what's already in the design docs.
- **Add via `/kb`.** The slash command at `.claude/commands/kb.md` is the canonical
  way to write entries — it produces a consistent shape and enforces append-only.
  Manual edits are allowed for the README itself; entries should go through `/kb`.

## How to use it

Run the command in a Claude Code session opened in the repo:

1. **Type `/kb`** at the prompt. The command does not accept arguments — it
   always asks interactively.
2. **Answer the domain question.** Valid answers are the four domain names
   (`harness`, `storage`, `dreaming`, `eval`) or `all` for a cross-cutting
   entry that touches every workstream (see *Cross-cutting entries* below).
3. **Review the draft.** The command reads the repo's current state (the
   contract docs, the ADRs for the chosen domain, recent commits, prior KB
   entries) and shows you a drafted entry. Add, edit, or remove anything that
   doesn't read right — you know things the artifacts don't. Say `skip` to
   abandon the run without writing.
4. **The command appends; you commit.** On confirmation the entry is appended
   to `KB-<domain>.md` (or to all four files for an `all` run, with a shared
   run id and cross-links). The command does **not** commit. Review the diff,
   then land the change through the normal PR workflow per
   [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — feature branch off `main`,
   push, open PR.

Re-run any time a new checkpoint is worth capturing — see *When to write an
entry* below for the signal.

## When to write an entry

Write one when something happens that a future collaborator would want context
on, and the context isn't already captured by the code, an ADR, or a PR
description:

- after a pivot or course correction,
- after a major decision was made in conversation (record the *why* and link
  the resulting ADR if there is one),
- at the end of a multi-session arc on a workstream,
- when a deferred item finally lands or is dropped.

Do not write entries on every commit — the KB is for state that's worth
re-reading 6 weeks later, not a changelog.

## Cross-cutting entries

When a change genuinely touches every workstream (a frozen-contract change, a
sprint-level decision, a process pivot), invoke `/kb` and answer `all` when
asked for the domain. The command writes one entry per domain with a shared
**run id** (an ISO-8601 timestamp) and cross-links the four entries via a
`Cross-domain run:` header line. Use this sparingly — most changes belong to a
single domain.

## Getting started — first-time setup

This section addresses the gotchas a new contributor will hit the first time
they try `/kb` after pulling the repo.

### Restart your Claude Code session after pulling

Claude Code discovers `.claude/commands/*.md` at session start. If you had a
session open during `git pull`, the `/kb` command won't appear until you restart
the session. Quit and reopen Claude Code in the repo, and `/kb` will be
registered.

### Permission prompts on the first run

The first time `/kb` runs in your environment, Claude Code will prompt for
permission on each underlying tool the command uses — roughly 5–10 prompts on
first invocation (Bash for `git`, `grep`, `mkdir`, `printf`; Read/Write/Edit for
files under `.kb/`). This is by design: the repo does not ship a pre-approved
allowlist, so every contributor sees and consents to what `/kb` does the first
time they run it.

For each prompt, the easiest path is to click **"always allow for this project"** —
the approval is recorded in your local `.claude/settings.local.json` (gitignored,
per-user, doesn't leak to teammates), and you won't see those prompts again in
this repo. Alternatively, accept per-invocation if you prefer not to persist any
allowlist.

### Always interactive — no arguments

The command does not accept arguments after the slash. Typing `/kb harness` will
not work; the command always asks interactively which domain you mean (or `all`
for a cross-cutting run). This is deliberate — the entry's domain is a
deliberate choice, not a flag.

### Which domain do I pick?

Pick the domain whose **workstream owns the decision or state** the entry
captures, not the domain whose code happens to sit at the cursor. A
cross-cutting change that touches every workstream is `all` (see *Cross-cutting
entries* above). If you're unsure, the four domain definitions live in
[`../docs/adrs/README.md`](../docs/adrs/README.md) and they apply here unchanged.

### Editing your own past entries

Don't. The journals are append-only by design — a later entry supersedes an
earlier one where they conflict, the same way a superseding ADR replaces an
earlier one without rewriting it. If a past entry is *wrong* (a typo, a misplaced
domain), write a new entry that corrects it and explains what was wrong; leave
the original in place as the historical record.

### The command appends; you commit

`/kb` does NOT commit. After the entry is written, review the diff and land it
through the normal PR workflow per
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) — feature branch off `main`, push,
open PR. This keeps KB changes reviewable like any other artifact.

### Ownership and review

`.kb/` follows the same per-domain convention as
[`../docs/adrs/`](../docs/adrs/): edit only your own domain's KB file, except
when writing a cross-cutting (`all`) entry. There is no CODEOWNERS enforcement
on `.kb/` — the convention is the rule. If you accidentally land an entry in
the wrong domain, write a corrective entry in the right domain rather than
editing or deleting the misplaced one.
