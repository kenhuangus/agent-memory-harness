---
id: ADR-dreaming-016
domain: dreaming
title: Console script renamed memory → daydream-cli to eliminate PATH-collision risk
status: Accepted
date: 2026-06-21
contract: true
supersedes: ADR-dreaming-008
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — informed (plugin shells out to this name)
origin: design session 2026-06-21 (halliday adversarial-review Finding #7)
---

# ADR-dreaming-016: Console script renamed `memory` → `daydream-cli` to eliminate PATH-collision risk

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** [`ADR-dreaming-008`](ADR-dreaming-008-memory-cli-console-script.md) · **Superseded by:** none

> **Scope of supersession.** This ADR carries forward all of ADR-008's
> structural decisions (standalone console script in
> `eval/memeval/dreaming/cli.py`, argparse, `daydream` extra install
> requirement, exit-code policy). It **replaces only the script name**
> (`memory` → `daydream-cli`) and the consequent plugin shell-out
> command. The literal `memory daydream` / `memory dream --all` text
> references in
> [`ADR-dreaming-001`](ADR-dreaming-001-daydreaming-stop-fired.md) and
> [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)
> are now slightly stale — tracked as an Open item below.

## Context
[`ADR-dreaming-008`](ADR-dreaming-008-memory-cli-console-script.md)
specifies `memory = "memeval.dreaming.cli:main"` as the console script
entry. Halliday (Finding #7, MED) flagged two real risks of the bare
name `memory`:

1. **PATH collision.** GNU `memory` (rare but exists), various memory-
   profiling tools, ad-hoc shell scripts named `memory`. `pip install`
   places our script on `$PATH`; the plugin's shell-out via
   `memory daydream …` may invoke the wrong binary silently. If the
   wrong binary echoes its argv, that's a leak vector.
2. **Distribution acceleration.** `pipx install` makes the entry
   point global. The plugin manifest is the thing distributed to other
   users; collision risk scales with adoption.

ADR-008's risk acknowledgement ("acceptable for a personal-machine
eval artifact") was correct for personal use but inadequate as the
plugin becomes installable for others.

## Options considered
- **Rename `memory` → `daydream-cli`** (chosen) — dream-prefixed,
  explicit "CLI" suffix, no plausible collision in common toolchains.
- Alternative names considered: `memory-dream`, `dream-cli`,
  `cookbook-memory`. `daydream-cli` won on (a) explicit subsystem
  naming (matches the function name from ADR-001 + ADR-005's "v1
  Daydream"), (b) unambiguous "this is a command-line tool" suffix,
  (c) low typing burden (12 chars).
- Keep `memory`; amend ADR-008 with abs-path-at-install policy —
  plugin resolves the binary by absolute path at install time. Every
  distribution mechanism (pip, pipx, bare install) needs the policy
  implemented; more operational complexity for marginal benefit.
- Defer entirely — accepts the risk; v1 audience IS personal-machine
  eval, but the plugin is the distribution surface and adoption only
  increases the risk over time.

## Decision
- Console script entry becomes `daydream-cli`:
  ```toml
  [project.scripts]
  memeval      = "memeval.cli:main"
  daydream-cli = "memeval.dreaming.cli:main"
  ```
- Subcommands are unchanged: `daydream-cli daydream …`,
  `daydream-cli dream --all`.
- Plugin's shell-out call updated: previously
  `memory daydream --session <id> --log <path>`, now
  `daydream-cli daydream --session <id> --log <path>`.

All other decisions in
[`ADR-dreaming-008`](ADR-dreaming-008-memory-cli-console-script.md)
stand: standalone console script (not a `memeval` subcommand), lives
at `eval/memeval/dreaming/cli.py`, argparse, exit-code policy,
`daydream` extra install requirement.

## Rationale
`daydream-cli` is collision-free in the toolchains we've surveyed (no
known executable of that name on common Linux/macOS distributions).
It's self-documenting on two axes: which subsystem (`daydream`) and
what kind of artifact (`-cli`). Renaming is structurally cheaper than
baking abs-path-at-install logic into every distribution channel —
one config edit + one plugin update vs N install-mechanism-specific
patches.

The 6-char cost (`memory` is 6 chars, `daydream-cli` is 12) is
trivial. The `daydream-cli daydream …` subcommand pattern is mildly
redundant — accepted because changing the subcommand would require
also superseding ADR-001's invocation contract; the rename ADR is
deliberately scoped to the entry-point name only.

The literal-text staleness in ADR-001/002 is bounded (those ADRs
describe *what* the CLI does, not its literal name as contract);
cleanup is a small successor when convenient.

## Tradeoffs & risks
- **`memory` → `daydream-cli` is a breaking change** for any consumer
  already invoking `memory` — currently zero in-tree (the CLI ships
  with the implementation PR; no consumers exist yet). External
  consumers don't exist yet either.
- **ADR-001 + ADR-002 text references** become stale on this change.
  Tracked below as an Open item (small text-update successor when
  convenient; not blocking implementation).
- **`daydream-cli daydream` subcommand pattern reads redundant.**
  Acceptable — the alternative is superseding ADR-001's invocation
  contract too, which is bigger scope than this ADR claims.
- **If we ever distribute multiple dream-related CLIs** (e.g., a
  separate `dream-cli` for night-only operations), the flat
  namespace becomes awkward. Not a v1 concern; revisit if/when.
- **Typo risk** vs the shorter `memory` — minor; shell completion
  mitigates.

## Consequences for the build
- **Contract — source of truth:** `eval/memeval/dreaming/cli.py`
  remains the implementation; the entry-point name is the
  `[project.scripts]` table in `eval/pyproject.toml`.
- **Shape:**
  ```toml
  [project.scripts]
  memeval      = "memeval.cli:main"
  daydream-cli = "memeval.dreaming.cli:main"
  ```
- **Shape — invocation:**
  - `daydream-cli daydream --session <id> --log <path> [--store <path>]`
  - `daydream-cli dream --all [--store <path>]`
- **Policy — plugin shell-out** uses `daydream-cli` (not `memory`).
  Plugin (Keith) updates the hook handler accordingly.
- **Policy — `daydream` extra** is still required for `daydream-cli`
  to be on `$PATH` (unchanged from ADR-008).
- **Exhaustive consumers:** Claude Code plugin Stop/PreCompact hooks
  (which now shell out via `daydream-cli daydream …`), and any ops
  surface invoking `daydream-cli dream --all` for night
  consolidation.

## Open items (dreaming-owned + cross-domain)
- **ADR-001 + ADR-002 text references** to `memory daydream` /
  `memory dream --all` are now slightly stale. Small text-update
  successor can either supersede those ADRs or — since the change is
  to literal text rather than the decision — add a brief
  annotation at the top of each pointing here. Defer until
  convenient.
- **Plugin shell-out update** is Keith's (harness-domain) when the
  plugin code lands; this ADR informs that coordination.
- **Pip vs pipx distribution-time PATH testing** — when the plugin
  ships, verify the entry-point name doesn't collide on the
  distribution channels actually used.
- **Subcommand redundancy** (`daydream-cli daydream`) — revisit if a
  successor decides to rename the subcommand for ergonomics; would
  need an ADR-001 supersession too.
