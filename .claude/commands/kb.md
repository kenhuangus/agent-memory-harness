---
description: Capture a project-story snapshot for one of the four workstream domains (harness/storage/dreaming/eval) as an append-only entry in `.kb/KB-<domain>.md`. Manually invokable any time a checkpoint is worth recording — after a pivot, a major decision, a milestone, or a multi-session arc that's worth preserving outside ADRs.
---

You are appending a project-story entry to the team's shared, repo-tracked knowledge
base under `.kb/`. Entries are **append-only** — never rewrite, never delete, never
`sed -i`. The KB is committed to the repo and visible to every collaborator; treat
it like any other tracked artifact.

# Phase 0 — Preflight

1. Confirm cwd is the root of a git repository:
   ```bash
   REPO_ROOT=$(git rev-parse --show-toplevel) || { echo "not in a git repo"; exit 1; }
   ```

2. Confirm `docs/adrs/README.md` exists — this is the source of truth for the four
   workstream domains. If absent, stop and tell the user the KB convention is
   coupled to the ADR taxonomy and needs that README first.

3. Parse the domain list from `docs/adrs/README.md`. Look for the `## Naming
   convention` section's domain table; extract the column-1 names (lowercase, no
   formatting). Expected set: `harness`, `storage`, `dreaming`, `eval`. If the
   parse yields anything different from those four, stop and tell the user — the
   ADR taxonomy has drifted and the KB command needs updating in lockstep.

4. Ensure `.kb/` exists at the repo root. Create it if missing:
   ```bash
   mkdir -p "$REPO_ROOT/.kb"
   ```
   On first creation, also write `.kb/README.md` (see Phase 5 template) before any
   journal entry.

5. Ask the user which domain this entry belongs to. The valid answers are the
   four parsed domain names *or* the literal string `all` (a **cross-cutting
   run** — see below). Wait for an explicit answer. Do not guess from cwd, recent
   file edits, or branch name — the entry's domain is a deliberate choice. Do
   not accept arguments after the slash command; the command always prompts
   interactively.

6. Determine the target file(s):
   - **Single-domain run**: target file is `$REPO_ROOT/.kb/KB-<domain>.md`.
   - **Cross-cutting run (`all`)**: target files are all four
     `$REPO_ROOT/.kb/KB-<domain>.md`. Every entry written in this run shares a
     single ISO-8601 timestamp (the **run id**) and each entry's header carries a
     `Cross-domain run:` line linking to the other three entries written in the
     same run.

   For each target file, note whether it already exists (first entry vs.
   subsequent entry for that domain).

# Phase 1 — Extract the story

Read the repo's current state to build the entry (or, for a cross-cutting run,
the entries). **Do not assume any specific artifact exists.** Discover what's
present and summarize from it.

For a **cross-cutting run**, the source material is read once but the *narrative
perspective* shifts per domain — each entry frames the same activity from its
domain's vantage point (e.g. a change to the recall surface reads as "MCP/skill
seam moved" for `harness` but as "router contract shape changed" for `storage`).
Do not copy-paste the same prose across domains; each entry stands on its own
for a reader who only opens that one file.

1. **Domain anchors** — the files that frame this domain regardless of which exist:
   - `prd.md`, `architecture.md`, `plan.md`, `CONTRIBUTING.md` at repo root.
   - `docs/adrs/README.md` and every ADR file for the target domain
     (`docs/adrs/ADR-<domain>-*.md`) — read titles, statuses, and the first
     paragraph of each. Do not copy bodies.
   - `.github/CODEOWNERS` for ownership boundaries.

2. **Recent activity** — read the last ~15 commits on the current branch with
   `git log --oneline -15` and the last ~5 with `git log -5 --stat`. Capture
   what's moved since the prior entry.

3. **Domain-specific surface** — scan repo root and `docs/` for markdown files
   whose name or contents reference the target domain (rg/grep is fine). Read
   anything that looks load-bearing; skip generated HTML, build artifacts, and
   ADRs already covered above.

4. **Prior KB state** — if `.kb/KB-<domain>.md` already exists, read the last
   entry in it. The new entry's "Notable since last entry" section is written
   *relative to that prior entry*.

From this material, draft the entry. The tone is *narrative for a collaborator
reading 6 weeks from now* — not a copy-paste of artifact bullets. If a section
genuinely has nothing to say at this moment, write one sentence acknowledging
that rather than padding.

# Phase 2 — Confirm

Show the user the drafted entry (or all four drafted entries, for a cross-cutting
run) in the format below and ask: anything to add, edit, or remove before
appending? Treat the user's response as authoritative — they know things the
artifacts don't (pivots, abandoned paths, conversations held in standup). Append
only on confirmation; if they say "skip", do not write.

For a cross-cutting run, the user may approve some domains and skip others —
honor partial approval. Domains they skip do not get an entry; domains they
approve are written together with the shared run id and cross-links *limited to
the approved set* (do not link to entries that weren't written).

# Phase 3 — Write the index README on first run only

If `.kb/README.md` does not exist, write it now (before any journal entry):

```markdown
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
```

# Phase 4 — Write the journal file header on first entry for a domain

If `.kb/KB-<domain>.md` does not exist, write its header first:

```markdown
# Knowledge Base — <domain>

**Domain owner:** <owner name from the ADR README table>
**First entry:** <today's date in YYYY-MM-DD>

Append-only journal of project-story snapshots for the **<domain>** workstream.
See [README.md](README.md) for conventions.

```

# Phase 5 — Append the entry (or entries)

Append using POSIX-safe append redirection. Never `>` (would overwrite). Never
`sed -i`.

```bash
printf '%s\n' "$entry" >> "$REPO_ROOT/.kb/KB-<domain>.md"
```

The `<N>` in each entry header is the entry count for *that domain file* (1, 2,
3, ...) — computed per file, not shared across domains. Count existing entries
in a file with:

```bash
grep -c '^## .* — entry ' "$REPO_ROOT/.kb/KB-<domain>.md" 2>/dev/null || echo 0
```

Increment by one for the new entry.

**Cross-cutting run mechanics:**
- Compute the run id (a single ISO-8601 timestamp at minute precision, e.g.
  `2026-06-22T14:35`) **once**, before drafting; every approved entry uses the
  same timestamp as its header `<ISO-8601 timestamp>`.
- After computing per-domain `<N>` values, render each entry's `Cross-domain
  run:` line with relative links to the other approved entries, using each
  target file's per-domain N. Example (harness entry in a four-domain run):
  `Cross-domain run: [KB-storage.md#entry-3](KB-storage.md), [KB-dreaming.md#entry-7](KB-dreaming.md), [KB-eval.md#entry-2](KB-eval.md)`
- Append all approved entries in one phase. If any single append fails, stop
  and report — do not retry blindly (an interrupted multi-write may leave the
  KB partially updated; the user needs to know).

## Entry template

```markdown
---

## <ISO-8601 timestamp YYYY-MM-DDTHH:MM> — entry <N>

**Triggered by:** <one-line reason: "post-pivot on X", "milestone: Y landed", "manual checkpoint after planning conversation", etc.>
**Branch:** <current git branch>
**Related ADRs:** <comma-separated ADR ids touched/relevant since last entry, or "none">
**Cross-domain run:** <comma-separated links to the other entries written in the same run, or omit this line entirely for a single-domain entry>

### Summary
One paragraph: what changed in this domain since the prior entry (or, for entry 1,
what the domain's current state is). Prose, not bullets — written for someone
reading 6 weeks from now without opening the linked artifacts.

### Key state
One paragraph naming the load-bearing decisions, contracts, and constraints
currently shaping the domain. Cite ADR ids inline (e.g. ADR-harness-006). Treat
this as a story the reader can follow; do not duplicate the ADR index.

### Open items
Bulleted: explicit deferrals, accepted-with-rationale review findings, known gaps,
or decisions the team has acknowledged but not yet ADR'd. One bullet per item,
one line each.

### Artifacts at time of entry
- <relative path to each load-bearing artifact that exists for this domain — e.g. `architecture.md`, `docs/adrs/ADR-<domain>-007-...md`>

### Notable since last entry (entries 2+ only)
A short bulleted list of what materially changed since the prior entry for this
domain — a major decision reversed, an ADR superseded, a new contract added, a
deadline shift. If nothing material changed, write "minor refinements only" and
skip the bullets. Omit this section entirely on entry 1.
```

# Phase 6 — Handoff

Report:
- file written (`.kb/KB-<domain>.md`),
- whether this was the first entry for the domain or a subsequent one (entry N),
- whether `.kb/README.md` was created this run,
- one-line summary of what the entry captured.

If the user added material in Phase 2 that isn't reflected in any tracked
artifact (e.g. "we decided in standup to drop X"), surface that explicitly so
they know it's now in the KB but not upstream. If the decision is load-bearing,
suggest writing an ADR for it.

Remind the user to commit `.kb/` changes via the normal PR workflow per
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) — KB entries are part of the repo's
history.

# Hard rules

- The `.kb/KB-<domain>.md` files are **append-only**. Never rewrite. Never
  `sed -i`. Never delete an entry. `.kb/README.md` may be edited manually but
  the command never touches it after first-run creation.
- Never write secrets, API keys, credentials, or production data into a KB
  entry. `.kb/` is tracked in git and visible to every collaborator.
- Do not fabricate content for missing artifacts. If an expected file isn't
  there, say so plainly in the entry rather than filling in plausible-sounding
  prose.
- Do not silently overwrite an existing file. Always append, even for the first
  entry (write the header, then the entry, all in append mode).
- The domain set is sourced from `docs/adrs/README.md`. If the ADR taxonomy
  changes, this command needs updating in lockstep — do not silently accept a
  new domain that isn't in the ADR table.
- Entries are written **only on explicit user invocation** of `/kb`. Do not
  auto-fire from other commands, hooks, or session events.
