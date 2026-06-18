# OpenCode Build, Run & Headless Architecture

> How to build, configure, run, and **headlessly drive** OpenCode from a Python
> eval harness over coding benchmarks. Source repo: `../opencode`.

## 1. Package architecture

| Package | npm name | Role |
|---|---|---|
| `packages/opencode` | `opencode` | **Core**: CLI entrypoint, HTTP/SSE server, session runner, tool execution, provider loading, config, agent logic. Everything to run the agent headlessly. |
| `packages/core` | `@opencode-ai/core` | Shared primitives: SQLite/Drizzle schema, EventV2 bus, ModelsDev client, session/project models, Effect layers, system-context. Required by `opencode`. |
| `packages/llm` | `@opencode-ai/llm` | Low-level LLM client: raw provider protocols (Anthropic, OpenAI…), auth, record/replay. |
| `packages/sdk/js` | `@opencode-ai/sdk` | TS/JS client SDK auto-generated from `openapi.json`: `createOpencodeClient`, `createOpencodeServer`, typed SSE. |
| `packages/server` | `@opencode-ai/server` | Shared HTTP middleware/routing (CORS, auth). Used by `opencode`'s server. |
| `packages/tui` | `@opencode-ai/tui` | Terminal UI (SolidJS + opentui). Not needed headless. |
| `packages/app` / `desktop` | `@opencode-ai/app` / `desktop` | Web SPA / Electron wrapper. Not needed headless. |
| `packages/plugin` | `@opencode-ai/plugin` | Plugin SDK types/interfaces. |
| `packages/ui` / `storybook` / `web` / `docs` / `console` / `stats` | — | UI libs / docs / sites / admin. Not needed headless. |
| `packages/function` / `enterprise` / `identity` / `slack` / `containers` | — | Cloud infra / SSO / integrations. |
| `packages/http-recorder` | `@opencode-ai/http-recorder` | Provider HTTP record/replay for tests. |
| `packages/effect-drizzle-sqlite` / `effect-sqlite-node` | — | Effect-wrapped SQLite adapters. |
| `packages/script` | `@opencode-ai/script` | Shared build/codegen scripts. |

**Headless-critical:** `packages/opencode`, `packages/core`, `packages/llm`,
`packages/sdk/js`.

## 2. Build & run locally

Runtime: **Bun `1.3.14`** (pinned `package.json:8`). Node.js does **not** run the
main `opencode` package (Bun-native APIs).

```bash
bun install                       # repo root

bun dev                           # dev (interpreted) — TUI in packages/opencode
bun dev /path/to/repo             # in a target repo
bun dev serve --port 8080         # headless API server, dev mode
```

Root `dev` (`package.json:9`) = `bun run --cwd packages/opencode --conditions=browser src/index.ts`.

Standalone binary (`CONTRIBUTING.md:56–70`):

```bash
./packages/opencode/script/build.ts --single
# → packages/opencode/dist/opencode-<platform>/bin/opencode
```

Installed binary: `opencode serve`, `opencode run "…"`, `opencode --help`.

## 3. Headless / non-interactive mode

**`opencode run` — one-shot** (`packages/opencode/src/cli/cmd/run.ts:1–14`
documents three modes; default is non-interactive):

```bash
opencode run "Implement the failing test" \
  --model anthropic/claude-haiku-4-5 \
  --format json \
  --dangerously-skip-permissions \
  --dir /path/to/repo
```

Flags (`run.ts:122–240`): `--model provider/model`, `--format json` (NDJSON
events to stdout), `--dangerously-skip-permissions` (auto-approve — essential
headless), `--dir`, `--session`/`--continue`, `--file`, `--agent`, `--attach
<url>`. Non-interactive mode auto-denies `question`/`plan_enter`/`plan_exit`
(`run.ts:365–383`). Exit when `session.status.type === "idle"` (`run.ts:727–730`).
Even without `--attach`, `run` creates an in-process server and routes via
`Server.Default().app.fetch(...)` (`run.ts:878–892`).

**`opencode serve` — persistent server** (`cli/cmd/serve.ts`):

```bash
opencode serve --port 4096 --hostname 127.0.0.1
```

Loads per-request via the `x-opencode-directory` header (`serve.ts:12`); pure
HTTP + SSE, no TUI.

**`opencode acp`** (`cli/cmd/acp.ts`): Agent Client Protocol over stdin/stdout.

## 4. Model / provider / API key configuration

Config precedence (low→high) (`packages/web/src/content/docs/config.mdx:44–53`):
remote well-known → `~/.config/opencode/opencode.json` → `OPENCODE_CONFIG` path →
project `opencode.json` → `.opencode/` → **`OPENCODE_CONFIG_CONTENT` env (best for
harness)** → macOS managed.

Schema (`config.mdx:357–392`):

```json
{ "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-haiku-4-5",
  "small_model": "anthropic/claude-haiku-4-5",
  "provider": { "anthropic": { "options": { "timeout": 600000 } } } }
```

Model string is `providerID/modelID`. API key loading:

- **Env (simplest):** `ANTHROPIC_API_KEY` — read via the catalog `provider.env`
  list (`provider/provider.ts:1471–1482`; Anthropic env at
  `packages/llm/src/providers/anthropic.ts:16`).
- **`OPENCODE_AUTH_CONTENT`** JSON (`auth/index.ts:59–63`):
  `'{"anthropic":{"type":"api","key":"sk-ant-…"}}'`.
- **`opencode providers`** interactive → `~/.config/opencode/data/auth.json`.

`--model` parsed as `providerID/modelID` (`run.ts:29–36`).

## 5. Capturing the full trajectory

**SSE** at `GET /event` (`server/.../groups/event.ts:14`), wrapped by the SDK
`client.event.subscribe()` (`sdk.gen.ts:1320–1343`).

Key events (`sdk/js/src/v2/gen/types.gen.ts`): `message.updated` (`:787`),
`message.part.updated` (`:803`), `session.status` incl. `{ type: "idle" }`
(`:1519`), `session.error` (`:1232`).

**Tokens & cost** — `StepFinishPart` (`types.gen.ts:553–571`), emitted as a
`message.part.updated` with `part.type === "step-finish"`:

```ts
type StepFinishPart = { type: "step-finish"; cost: number; tokens: {
  total?: number; input: number; output: number; reasoning: number;
  cache: { read: number; write: number } } }
```

**Tool calls** — `message.part.updated` with `part.type === "tool"`, status
`running` → `completed`/`error`.

**`--format json`** writes every part-update as an NDJSON line to stdout
(`run.ts:175–178`). After a run, `GET /session/{id}/message` returns full history
with all parts (incl. cumulative `step-finish` tokens/cost).

## 6. Runtime & key dependencies

| Requirement | Version |
|---|---|
| Bun | `1.3.14` (pinned) |
| TypeScript | `5.8.2` (via `tsgo`) |
| Effect | `4.0.0-beta.74` |
| Vercel AI SDK | `ai@6.0.168` + `@ai-sdk/*` |
| Hono | `4.10.7` |
| Drizzle ORM | `1.0.0-rc.2` + `@effect/sql-sqlite-bun` |
| SolidJS / opentui | `1.9.10` / `0.3.4` (UI only) |

The Python harness does **not** run Bun itself — it needs `opencode` on `PATH`.

## How the Python harness can drive OpenCode headlessly

### Approach A — one-shot CLI (recommended first)

```python
import subprocess, json, os
result = subprocess.run([
    "opencode", "run",
    "--model", "anthropic/claude-haiku-4-5",
    "--format", "json",
    "--dangerously-skip-permissions",
    "--dir", "/path/to/benchmark/repo",
    "Fix the failing test in tests/test_foo.py",
], capture_output=True, text=True,
   env={**os.environ, "ANTHROPIC_API_KEY": api_key})
events = [json.loads(l) for l in result.stdout.splitlines() if l]
```

Stateless per task; trajectory-complete (`step-finish` tokens/cost + `tool` parts
in the NDJSON); permission-safe; isolated by `--dir`.

### Approach B — persistent server + HTTP/SSE

```python
proc = subprocess.Popen(["opencode", "serve", "--port", "4096"],
  env={**os.environ, "ANTHROPIC_API_KEY": api_key,
       "OPENCODE_CONFIG_CONTENT": json.dumps({"model": "anthropic/claude-haiku-4-5"})})
# httpx: POST /session, subscribe GET /event, POST /session/{id}/message,
# read until session.status == idle, x-opencode-directory header per repo.
```

Amortizes startup across many tasks; supports session resumption; multi-tenant via
`x-opencode-directory`.

### Approach C — JS/TS SDK

`createOpencodeServer()` + `createOpencodeClient()` (`sdk/js/src/v2/server.ts`,
`example/example.ts`) if any harness component is TypeScript.

### Recommendation

**Start with Approach A** for the SWE-Bench-CL runs (stateless, trajectory in the
NDJSON, easy to map onto the harness `Trajectory`/`TrajectoryStep` schema). Move to
**Approach B** when we need session reuse across a batch. The memory system itself
lives in the **OpenCode plugin** ([`05`](05-integration-strategy.md)), enabled via
config exactly as a developer would — the harness does not host memory; it only
points the run at a fresh store path (per-version isolation) and reads NDJSON to
grade/log. Model via `--model anthropic/claude-haiku-4-5` (or
`OPENCODE_CONFIG_CONTENT`); key via `ANTHROPIC_API_KEY` in the subprocess env.

## Summary

OpenCode is a Bun 1.3.14 TS monorepo; `packages/opencode` is the runnable core and
`packages/sdk/js` the typed client. `opencode run --format json
--dangerously-skip-permissions --dir <repo> --model anthropic/claude-haiku-4-5
"<prompt>"` is a first-class non-interactive mode that streams NDJSON trajectory
events to stdout and exits on idle — directly drivable as a subprocess from the
Python harness. `opencode serve` provides a persistent HTTP/SSE server (default
`127.0.0.1:4096`) for session reuse and event observation. Model is
`providerID/modelID`; the Anthropic key comes from `ANTHROPIC_API_KEY`. Each LLM
turn emits a `step-finish` part with `tokens.{input,output,reasoning,cache}` and
`cost`, and each tool call emits a `tool` part — giving the harness complete,
gradeable trajectory visibility that maps cleanly onto our `TrajectoryStep` model.
