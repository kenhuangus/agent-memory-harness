---
id: ADR-harness-014
domain: harness
title: Cursor sandbox isolation via HOME (not CURSOR_DATA_DIR) + keychain-free CURSOR_API_KEY auth, enabling per-stage parallel runs
status: Proposed
date: 2026-06-26
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: docs/harnesses/06-cursor-cli.md §7/§9
---

# ADR-harness-014: Cursor sandbox isolation via `HOME` + `CURSOR_API_KEY` auth

**Status:** Proposed · **Date:** 2026-06-26 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
The eval pipeline runs several **stages** that must not see each other's state: the
memoryless `base` stage must have **no** memory MCP server; the `plugin` stages
**must**; and concurrent stages must not collide on a shared MCP approved-list, auth,
or store. For Claude Code on macOS this is constrained — the config dir is isolatable
(`CLAUDE_CONFIG_DIR`) but auth is **keychain-bound**, so a fresh sandbox needs an
interactive `/login` and can't be cloned; hence today **all** Claude pipeline runs
share one sandbox (see the recorded shared-sandbox constraint).

Two facts about Cursor — **both verified against the installed binary, correcting an
earlier assumption in [`docs/harnesses/06-cursor-cli.md`](../harnesses/06-cursor-cli.md)** —
change the calculus:

1. **`CURSOR_DATA_DIR` does NOT isolate config.** It only relocates the
   `~/.local/share/cursor-agent` *data* area (transcripts/projects). `mcp.json`,
   `cli-config.json` (the MCP approved-list + permissions), and auth all resolve from
   a hardcoded `homedir()/.cursor/…`. Verified: `CURSOR_DATA_DIR=/empty` still saw
   the host's MCP servers **and** the host login; `HOME=/empty` saw **no MCP servers
   and "Not logged in."**
2. **Auth is platform-split.** The binary's auth priority is `auth-token → api-key →
   login`; the env vars (`CURSOR_AUTH_TOKEN` / `CURSOR_API_KEY`) are the headless
   source. **On Linux** credentials are a file store (`auth.json`), no keychain — so
   `CURSOR_API_KEY` + an isolated `HOME` runs cleanly (this is the VPS/CI experience).
   **On macOS, however** — corrected after end-to-end testing, the env key does **not**
   bypass the keychain: the binary unconditionally probes the login keychain at
   startup (`security add-generic-password` "cursor-keychain-probe") **even with an API
   key**, and in an isolated `HOME` that probe **hangs with no TTY** ("Keychain
   operation timed out after 30000ms" / "Security process exited 154"). The fix is to
   give the sandbox its **own** unlocked, empty login keychain
   (`security create-keychain` + `unlock-keychain` →
   `<HOME>/Library/Keychains/login.keychain-db`); the probe then writes there with no
   prompt and the API key authenticates (verified: real `cursor-agent` returns from an
   isolated sandbox). There is no `~/.cursor/auth.json` on a logged-in macOS host, so
   a *copied* sandbox still doesn't carry login.

## Options considered
- **`CURSOR_DATA_DIR` per sandbox** (the original plan). Rejected: verified
  insufficient — it leaves `mcp.json`/auth pointing at the host, so the baseline
  stage would inherit the host's memory MCP and the run would mutate the developer's
  real `~/.cursor`.
- **Copy a logged-in sandbox dir per stage.** Rejected: on macOS the login lives in
  the keychain, not a file, so a copied dir is logged out — the exact trap we hit
  with Claude Code.
- **`HOME` per stage + `CURSOR_API_KEY` env** (chosen). A fresh `HOME` relocates
  `~/.cursor/{mcp.json,cli-config.json,auth}` in one move; the API key authenticates
  every stage from the same env value with no keychain and no interactive login.
  Optionally also set `CURSOR_DATA_DIR` to capture transcripts inside the sandbox.

## Decision
The Cursor adapter isolates each run with a **fresh `HOME`** (its own
`HOME/.cursor/{mcp.json,cli-config.json}`) and authenticates with **`CURSOR_API_KEY`
from the environment** (loaded from `.env` via the existing `dotenv_loader`). It
**additionally** sets `CURSOR_DATA_DIR` to a per-sandbox path so transcripts land in
isolation, and **on macOS provisions a dedicated unlocked login keychain inside the
sandbox `HOME`** so the binary's startup keychain probe doesn't hang (a `darwin`-only
no-op on Linux, which uses a file credential store). Because auth is a shared env key
(not a per-sandbox login) and config is a per-`HOME` directory, **pipeline stages get
fully independent MCP/approval/auth and can run in parallel** — the per-run-sandbox
model we could not have on Claude-Code-macOS.

## Rationale
This is the one-sentence defense: **`HOME` is the only thing that actually relocates
`mcp.json`+auth (proven by test), and `CURSOR_API_KEY` is the only auth path that
doesn't touch the keychain (proven in the binary)** — together they give cheap,
correct, parallel per-stage isolation. It also keeps the developer's host `~/.cursor`
untouched, which matters because `cursor-agent mcp enable` mutates a *persistent*
approved-list under the active config dir (we hit exactly this during research).

## Tradeoffs & risks
- **macOS keychain probe (the surprise that end-to-end testing caught).** The clean
  "API key bypasses the keychain" story holds on **Linux only**. On macOS the binary
  probes the login keychain at startup regardless of the API key, which hangs in an
  isolated `HOME`. We accept a `darwin`-gated mitigation: provision a throwaway,
  unlocked, empty `<HOME>/Library/Keychains/login.keychain-db` in the sandbox builder
  (verified to make a real run succeed). Risk: a future Cursor build could change the
  probe; mitigation is fail-open (no keychain → the run still attempts auth and
  surfaces a clear error) and the behavior is re-verified per version. Linux/CI is
  unaffected — the keychain step is a no-op there.
- **One-time setup cost:** a Cursor API key from `cursor.com/dashboard`, placed in
  `.env` as `CURSOR_API_KEY`. Documented in `.env.example`. Without it the adapter
  fails fast with a clear message (not a confusing "Not logged in" mid-run).
- **API-key billing vs subscription.** Unlike the Claude path (which strips API keys
  to force subscription auth), Cursor's headless path *is* the API key, which may bill
  differently than an interactive Cursor subscription. Accepted and called out: it is
  the only keychain-free headless auth, and it is the price of parallel isolation.
- **A bare `HOME` is very empty** — no shell rc, no git identity. The adapter must
  set only what `cursor-agent` needs and must not assume host dotfiles. Mitigation:
  the sandbox builder writes just `HOME/.cursor/*`; the working directory (the code
  checkout) is passed as `cwd`/`--workspace`, independent of `HOME`.
- **Self-update writes under `HOME`/data.** Harmless but noted; pin or pre-warm if a
  run must be hermetic.

## Consequences for the build
- **Policy — isolation env:** the Cursor sandbox sets `HOME=<sandbox>` (relocates
  `.cursor/mcp.json` + `cli-config.json` + auth) and `CURSOR_DATA_DIR=<sandbox>/data`
  (transcripts). It **never** relies on `CURSOR_DATA_DIR` alone for config isolation.
- **Policy — auth:** `CURSOR_API_KEY` is read from the environment (`.env` via
  `load_root_dotenv`) and passed through to the `cursor-agent` subprocess env. The
  adapter asserts it is set before any real run and never logs its value.
- **Policy — never touch host config:** the adapter must not run
  `cursor-agent mcp enable` (or any state-mutating subcommand) without `HOME` pointed
  at the sandbox first. A guard checks `HOME != real-home` before such calls.
- **Policy — parallel stages allowed:** because each stage's `HOME` is independent,
  the Cursor pipeline path may run stages concurrently (one `HOME` per stage),
  unlike the Claude shared-sandbox path. `MEMORY_STORE` still isolates the store per
  run as on every harness.
- **Cross-link:** the MCP approval gate that this isolation makes safe is in
  [`ADR-harness-015`](ADR-harness-015-cursor-mcp-wiring-approval-gate.md); the
  backend itself in
  [`ADR-harness-013`](ADR-harness-013-cursor-cli-second-harness.md).
