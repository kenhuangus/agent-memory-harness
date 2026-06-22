---
id: ADR-dreaming-019
domain: dreaming
title: $MEMORY_STORE is a directory (not a file-sentinel) — auto-mkdir; ValueError if it's a file
status: Accepted
date: 2026-06-22
contract: true
supersedes: ADR-dreaming-015
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-22 (post-PR5 review — file-sentinel design caught as cross-domain accident)
---

# ADR-dreaming-019: `$MEMORY_STORE` is a directory (not a file-sentinel) — auto-mkdir; ValueError if it's a file

**Status:** Accepted · **Date:** 2026-06-22 · **Contract:** yes
**Supersedes:** [`ADR-dreaming-015`](ADR-dreaming-015-filesystem-state-management.md) (partial — §1 basedir resolution rule only) · **Superseded by:** none

> **Scope of supersession.** This ADR replaces ONLY
> [`ADR-dreaming-015`](ADR-dreaming-015-filesystem-state-management.md) §1
> (the basedir resolution rule that required `MEMORY_STORE` to point at a
> file). The other three sections of ADR-015 stand unchanged:
> §2 (uniform 30-day retention TTL), §3 (sweeper-on-invocation throttled),
> §4 (env-var overrides `DREAM_RETENTION_DAYS` / `DREAM_SWEEP_INTERVAL_MIN`).

## Context

ADR-015 §1 enforced `MEMORY_STORE` as a path to a **file** whose `.parent`
became the dreaming basedir. Reading the ADR carefully: the rule was
justified solely by *"per the existing
[`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md) assumption, now
enforced."* No load-bearing technical rationale was given for the file
shape; it was inherited from an earlier assumption and codified for
consistency. Three independent forces have surfaced since PR5 landed that
make the file-sentinel design wrong:

1. **Live cross-domain conflict.** Keith's plugin
   ([`plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json`](../../plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json)
   + [`plugin/README.md`](../../plugin/README.md) line 78) sets
   `MEMORY_STORE=./.cookbook-memory` — a **directory**. The dreaming
   engine's `_state.resolve_basedir()` rejects that with
   `ValueError("must point to a file, got a directory")`. The conflict
   doesn't surface yet only because `hooks_handler.handle()` is still a
   no-op; the moment that handler is wired to call `daydream-cli`
   (the deferred migration tracked in PR #48's body), the v1 functional
   loop breaks on first hook fire.

2. **Semantically wrong shape.** `MEMORY_STORE` is the env var that
   means "where memory lives." A directory is the natural shape for "a
   place." A sentinel file the user must `touch` before the system works
   is a cargo-cult that no current backend actually needs:
   [`MarkdownStore`](../../eval/memeval/stores/markdown_store.py) writes
   markdown files under its root (needs a directory),
   [`SqliteVectorStore`](../../eval/memeval/stores/sqlite_store.py) wants
   its own `.db` file (which lives inside the basedir), the dreaming
   engine writes `<basedir>/dream/*` (needs a directory). No backend
   reads the file at `MEMORY_STORE` itself.

3. **PR5 cli.py code contortion.** The PR5 wiring is
   `MarkdownStore(Path(MEMORY_STORE).parent / "markdown")` — a
   `Path.parent` indirection that exists *only* because `MEMORY_STORE`
   is a file. Under the directory model the line collapses to
   `MarkdownStore(MEMORY_STORE / "markdown")`. The indirection is the
   smell.

A fourth, downstream motivation: the bench/eval use case (raised in the
post-PR5 review) wants a checked-in, version-controlled store directory
under `eval/tests/fixtures/` so two devs running the same benchmark see
the same memory state. A directory is the natural shape for that
artifact; a file-sentinel-plus-sibling-data layout is not.

## Options considered

- **`MEMORY_STORE` is a directory; auto-mkdir on missing; ValueError if it
  exists and is a file** (chosen). The env var IS the basedir. The
  engine creates it idempotently with
  `mkdir(parents=True, exist_ok=True)` on first use. The only error mode
  is "you pointed at a regular file by mistake."
- **`MEMORY_STORE` is a directory; FileNotFoundError if missing** (no
  auto-mkdir). Stricter: forces the user to acknowledge the path with an
  explicit `mkdir` before the system runs. Symmetric to ADR-015's
  "FileNotFoundError if file missing." Rejected: the friction has no
  benefit. `mkdir(exist_ok=True)` is idempotent and safe; the plugin's
  `_Engine` already does the same thing
  ([`plugin/cookbook_memory/core/client.py:67`](../../plugin/cookbook_memory/core/client.py)
  — `root.mkdir(parents=True, exist_ok=True)`). Aligning the two paths is
  cheap.
- **Keep ADR-015's file-sentinel; amend the plugin convention to match.**
  Rejected: the file-sentinel is the side with no load-bearing reason.
  Asking Keith to amend `plugin/README.md` + `hooks.json` to a
  `.cookbook-memory/.sentinel` workaround would propagate the cruft.
- **Two env vars** (`MEMORY_STORE_FILE` for ADR-015's file pattern;
  `MEMORY_STORE_DIR` for the directory). Rejected: doubles the surface
  for zero gain; the underlying question is "where do memories live,"
  which has one answer.

## Decision

`$MEMORY_STORE` is a **directory path**. `_state.resolve_basedir()`
becomes:

```python
def resolve_basedir() -> Path:
    """Return $MEMORY_STORE as a directory, creating it idempotently."""
    raw = os.environ["MEMORY_STORE"]              # KeyError if unset
    basedir = Path(raw).resolve()
    if basedir.exists() and not basedir.is_dir():
        raise ValueError(
            f"MEMORY_STORE must be a directory, got a file: {basedir}"
        )
    basedir.mkdir(parents=True, exist_ok=True)
    return basedir
```

Specifically:

- **Unset → `KeyError`** (unchanged from ADR-015 §1).
- **Missing path → auto-mkdir** (CHANGED — was `FileNotFoundError`).
- **Existing file at the path → `ValueError`** (INVERTED from ADR-015 §1
  — the old rule raised on directories; the new rule raises on files).
- **Existing directory → use it directly** (CHANGED — was "use its parent").

`<basedir>/dream/`, `<basedir>/markdown/`, and every other per-backend
subdir live under `MEMORY_STORE` itself. No `.parent` indirection.

## Rationale

The honest one: ADR-015's file-sentinel design was inherited from an
ADR-harness-004 assumption with no load-bearing rationale, and it
collided immediately with the plugin convention the moment Keith's
adapter shipped. There is no use case the file shape enables that the
directory shape doesn't, and the directory shape simplifies code (drops
a `Path.parent`), aligns with the plugin tree (which already uses a
directory), and matches the natural shape of every store backend that
exists. The fix is a 7-line change to `resolve_basedir()` and a 1-line
simplification in `cli.py._make_store()`.

Auto-mkdir matches the plugin's `_Engine` (which already does
`root.mkdir(parents=True, exist_ok=True)`), removes a user-friction step
("did I remember to `touch` the file?"), and is idempotent. The
ValueError-on-file mode is the only honest error: it catches the case
where a user follows ADR-015's stale convention by habit.

## Tradeoffs & risks

- **All existing tests that set `MEMORY_STORE` to a touched file now
  raise ValueError.** PR4's `_state` tests + PR5's `memory_store_file`
  fixture both need updating. Counted as part of this ADR's impl scope.
- **Stale `MEMORY_STORE` env var on a developer's machine** (pointing
  at the old file) raises ValueError on the next CLI invocation.
  Mitigation: the error message names the path and says "must be a
  directory" — actionable. Documented in this ADR + the `.env.example`.
- **Auto-mkdir on a typo** (e.g. `MEMRRY_STORE=/wrong/path`) silently
  creates an empty wrong-place directory. Accepted: same risk applies
  to any auto-create directory contract; the cost of the friction
  alternative (explicit `mkdir` before each run) is higher. Mitigation:
  the `daydream.cli_resolved` event (PR5 criterion 36) records the
  resolved path in every run's diary, so a post-incident audit
  identifies where data went.
- **Bench reproducibility is unlocked, not solved.** Making
  `MEMORY_STORE` a directory makes it *possible* to check a default
  store dir into the repo (e.g.
  `eval/tests/fixtures/dreaming_store/`). The choice of whether to
  actually do that, and at what path, is a successor ADR — not in
  scope here.
- **Older `.env.example` from PR #50** carries the file-sentinel
  convention (`/absolute/path/to/store.jsonl`). Updated by the same PR
  that lands this ADR.

## Consequences for the build

- **Contract — source of truth:** `_state.resolve_basedir()` in
  `eval/memeval/dreaming/_state.py`. The function's docstring is the
  durable record of the directory contract.

- **Shape:**
  - `MEMORY_STORE` env var → an absolute directory path.
  - `<basedir>/dream/` → daydream per-session state (unchanged).
  - `<basedir>/markdown/memory/<item_id>.md` → MarkdownStore docs
    (clarified — was `<basedir>/markdown/memory/...` already, just
    without the basedir contortion).

- **Policy — auto-mkdir.** The engine creates `MEMORY_STORE` on first
  use if it doesn't exist. No explicit user `mkdir` required. Symmetric
  to `<basedir>/dream/` already being auto-created (ADR-015 §1, retained).

- **Policy — `dirname` is forbidden.** Any caller computing
  `Path(MEMORY_STORE).parent` is using the superseded shape. The PR
  landing this ADR audits the tree for that pattern.

- **Policy — cross-domain alignment.** Keith's plugin convention
  (`MEMORY_STORE=./.cookbook-memory`) now Just Works without amendment.
  The migration of `daydream-cli` into the plugin tree (PR #48's
  deferred follow-up) loses one obstacle.

- **Exhaustive consumers:**
  - `eval/memeval/dreaming/_state.py:resolve_basedir` (the
    implementation)
  - `eval/memeval/dreaming/engine.py:daydream` (calls
    `resolve_basedir`)
  - `eval/memeval/dreaming/cli.py:_handle_daydream`,
    `_handle_dream`, `_make_store` (the PR5 CLI; the
    `Path(MEMORY_STORE).parent` indirection in `_make_store` is dropped)
  - `eval/memeval/dreaming/tests/test_state.py` (PR4 tests covering the
    old error modes)
  - `eval/memeval/dreaming/tests/test_cli.py` (PR5 `memory_store_file`
    fixture and the threading tests that capture MEMORY_STORE during
    the engine call)
  - `.env.example` (PR #50 — the example value)
  - `plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json`
    (Keith's directory convention — now contract-compliant)

## Open items

- **Default store location for bench/eval.** Making `MEMORY_STORE` a
  directory unlocks the question "should the bench default to a
  checked-in directory at `eval/tests/fixtures/dreaming_store/` so two
  devs see the same memory state?" Tracked separately; not in scope
  here. Decision belongs to a cross-domain conversation with Brent
  (storage) and Ken (eval).
- **The `daydream-cli` → plugin-tree migration** (PR #48's deferred
  follow-up) becomes simpler — one of the contract incompatibilities
  is now resolved. Timing of that migration is unchanged.
- **Successor ADR or ADR-015 amendment for the §2/§3/§4 sections.**
  Those sections stand; if any of them ever needs to change, the next
  ADR can address the file-sentinel residue in their language
  (`<basedir>/dream/*` references etc. remain accurate, but the
  framing of "MEMORY_STORE's parent" in the ADR-015 body is now stale —
  historical-record only per the "never edit accepted ADR" rule).
