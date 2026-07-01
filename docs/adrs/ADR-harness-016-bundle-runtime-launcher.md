---
id: ADR-harness-016
domain: harness
title: Bundle runtime launcher — the plugin bootstraps its own Python runtime, so the standard `claude plugin install` is the whole install
status: Accepted
date: 2026-07-01
contract: false
supersedes: none
superseded_by: none
owner: Keith (P1)
origin: production-install hardening 2026-07-01 (make the plugin installable with one standard command)
---

# ADR-harness-016: Bundle runtime launcher — the plugin bootstraps its own Python runtime

**Status:** Accepted · **Date:** 2026-07-01 · **Contract:** no
**Supersedes:** none · **Superseded by:** none

## Context
ADR-harness-010 made the plugin installable from git (`claude plugin marketplace add`
reads the repo-root manifest; the committed bundle at
`plugin/marketplace/cookbook-memory/` ships inside the marketplace clone). But the
bundle's commands still assumed a host Python runtime: `.mcp.json` ran
`python3 -m cookbook_memory mcp` and every hook tried `memory-hook || python3 -m …`.
A user who ran only the two `claude plugin` commands got a plugin whose MCP server
and hooks silently no-op'd until they *also* did a `pip install --user` from the git
URL — a second, non-obvious, non-Claude step that contradicts "install with one
standard command."

Two additional facts from install verification (2026-07-01, Claude Code 2.1.197):

1. The root manifest's `git-subdir` plugin source resolves after an explicit
   `marketplace add`, but the one-command form
   (`claude plugin install cookbook-memory@kenhuangus/agent-memory-harness`)
   fails to find the plugin in the auto-added marketplace. A **relative-path
   source** (`"./plugin/marketplace/cookbook-memory"`) resolves inside the
   marketplace clone itself — the standard community-marketplace pattern — and
   avoids the second git fetch entirely.
2. Claude Code expands `${CLAUDE_PLUGIN_ROOT}` in plugin `.mcp.json` and
   `hooks.json` commands, and `claude plugin install`'s copy into the plugin cache
   preserves a bundled script's executable bit — so the bundle can carry its own
   entry point.

## Options considered
- **Ship a launcher script in the bundle that finds-or-bootstraps the runtime**
  (chosen): `.mcp.json` and every hook invoke
  `${CLAUDE_PLUGIN_ROOT}/bin/cookbook-memory`, a POSIX-sh launcher that resolves the
  console scripts in order — explicit override dir → `$PATH` → a managed venv at
  `~/.cookbook-memory/runtime` that it creates on first use (`uv` when available,
  else `python3 -m venv` + pip) from the repo's git URL. Hooks never block on the
  bootstrap (background, fail-open); the MCP path bootstraps synchronously and says
  what it is doing on stderr.
- **Keep the documented two-step install (pip + plugin)**: honest but permanently
  non-standard; every new user pays the "why is recall empty?" debugging tax when
  they skip the pip step. Rejected — it is exactly the friction this ADR exists to
  remove.
- **`uvx --from git+…` directly in the manifests**: one line, no launcher — but hard
  a dependency on `uv`, re-resolves the environment per invocation, and gives hooks
  no fail-open/no-block control. Rejected; the launcher *uses* uv when present.
- **Vendor the Python code into the bundle**: no bootstrap at all, but the engine's
  dependency tree (MCP SDK, storage, dreaming extras) cannot be vendored as a flat
  file copy, and it forks the package. Rejected.

## Decision
1. **The bundle carries a runtime launcher** at `bin/cookbook-memory` (committed
   adapter source, copied by `build-bundle`, shipped executable). `.mcp.json` runs
   `${CLAUDE_PLUGIN_ROOT}/bin/cookbook-memory mcp`; every hook runs
   `"${CLAUDE_PLUGIN_ROOT}/bin/cookbook-memory" hook <Event>`.
2. **Resolution order:** `$COOKBOOK_MEMORY_BIN_DIR` override → `memory-cli` /
   `memory-hook` on `$PATH` → managed venv at `~/.cookbook-memory/runtime`
   (`$COOKBOOK_MEMORY_RUNTIME`), bootstrapped on first use from
   `cookbook-memory[mcp] @ git+<repo>#subdirectory=plugin` (`$COOKBOOK_MEMORY_SPEC`).
   Single-flight via a lock dir; log at `<runtime>/bootstrap.log`.
3. **Fail-open split:** `hook` mode always exits 0 and never blocks a session on a
   network install (bootstrap runs detached in the background — ADR-harness-006);
   `mcp` mode may fail loudly with the log path (Claude Code surfaces a failed MCP
   server; the next session retries).
4. **The root marketplace manifest uses a relative-path source**
   (`"./plugin/marketplace/cookbook-memory"`) instead of `git-subdir`, so the plugin
   resolves inside the marketplace clone — including via the one-command
   `claude plugin install cookbook-memory@kenhuangus/agent-memory-harness` form.

A pre-existing host install (pip --user, dev venv via
`memory-cli install-claude-plugin`) keeps working and always wins over the managed
venv; the launcher is the fallback that makes the zero-prerequisite path real.

## Rationale
The plugin's whole pitch is "add memory to your agent with one native install." The
launcher makes the standard Claude Code install self-sufficient on the two runtimes
we support (macOS/Linux) while preserving every existing pinned-runtime path as a
short-circuit. Putting the resolution logic in one committed sh file — rather than
in `||` chains inside JSON manifests — gives it tests, a log, a lock, and one place
to change.

## Tradeoffs & risks
- **First-use network install**: the managed bootstrap pulls the package tree from
  git at session start. Mitigations: it never blocks (hooks background it; recall is
  fail-open-empty until ready), it is single-flight, logged, and skipped entirely
  when a host runtime exists. `rm -rf ~/.cookbook-memory/runtime` resets a broken
  bootstrap.
- **The managed venv tracks `main` at bootstrap time**, not the installed plugin
  version — a skew risk between bundle manifests and Python code. Accepted while
  both ship from one repo on `main`; pin `$COOKBOOK_MEMORY_SPEC` (or the spec
  default) to a tag when releases start being cut.
- **POSIX-only**: no Windows (`.cmd`) launcher. Accepted; documented. Windows users
  can pre-install the package so `$PATH` resolution wins.
- **First session may show the MCP server as failed** if it starts before the
  hook-triggered bootstrap finishes; it self-heals on the next session/reconnect.
  Documented in the README.

## Consequences for the build
- **Policy:** `bin/` is a bundle ingredient — `build-bundle` copies it, restores the
  executable bit, and `validate_bundle` fails a bundle whose launcher is missing or
  non-executable. The launcher ships in the wheel via package-data.
- **Policy:** manifests reference the launcher only through `${CLAUDE_PLUGIN_ROOT}`
  (quoted in hook commands — cache paths may contain spaces).
- **Policy:** the root `.claude-plugin/marketplace.json` uses a relative-path plugin
  source; the drift-guard test asserts it points at the committed bundle.
- The pinned local-dev install (`_pin_runtime_commands`) is unchanged: it still
  rewrites commands to absolute console scripts, bypassing the launcher.
