# Memory Integration Strategy — OpenCode × Cookbook Memory

> Owner: **P1 Keith**. Ties the OpenCode research
> ([`01`](01-agent-loop.md)–[`04`](04-build-run-architecture.md)) to our design
> intent. A proposal, not a freeze — the contract is
> [`../../architecture.md`](../../architecture.md) + `eval/memeval/protocols.py`.
>
> **OpenCode is the *reference* adapter** of a portable, multi-harness memory core
> (Claude Code and Codex are the other two). The shared-core architecture and the
> capability comparison live in
> [`../harnesses/`](../harnesses/01-cross-harness-comparison.md); this doc is the
> OpenCode-specific instance of it. The portable core is the **MCP `recall`/`remember`
> server** + store + router + dreaming; OpenCode's per-turn `system.transform`
> injection is an *adapter enhancement* on top of the (universal) model-pulled
> `recall` tool, not a core requirement.

## 0. Two non-negotiables that drive the whole design

1. **Memory is a real, native OpenCode capability — not a harness backdoor.**
   A human developer running OpenCode normally must get the memory system for free
   by enabling it the same way they enable any OpenCode extension. We must **not**
   give OpenCode a bespoke seam that only the test harness can call. The harness
   adapts to OpenCode (and may be modified freely); OpenCode is driven exactly as a
   real user would drive it.

2. **Memory is isolated per framework version — each version earns its own
   memories.** Version `vN+1` starts from an **empty store** and may not inherit
   anything `vN` learned. Isolation is enforced by a **per-run store path** (see
   §3); no code is version-aware — a run just points at a fresh, empty store.

These two together rule out the earlier "drive via NDJSON and record memory on the
Python side" framing: that made memory a harness artifact. Instead, memory lives
**inside OpenCode's extension surface**, and the harness simply runs OpenCode with
it enabled, pointed at a fresh store.

## 1. Where memory lives: a distributable OpenCode plugin (fork as fallback)

**Decision: build it as an OpenCode plugin** (`@cookbook/memory`, installable via
the `plugin` config key or `.opencode/plugin/`). Rationale: the plugin can outlive
this project as a genuine contribution to OpenCode, and a developer gets it with a
one-line config entry. **The bar:** the plugin must match what a true forked
integration would give us; we do **not** compromise the memory system to fit the
plugin model.

The hook surface clears that bar (`packages/plugin/src/index.ts:222–335`):

| Memory need | Plugin hook | Equivalent to a fork? |
|---|---|---|
| **Forced pre-generation injection** of retrieved memory into the prompt | `experimental.chat.system.transform` → mutable `output.system: string[]`, with `sessionID` + `model` (`index.ts:291`) | **Yes** — same `system[]` a fork's `SystemContext` source feeds. |
| Inject into history instead of system | `experimental.chat.messages.transform` (`index.ts:282`) | Yes |
| Register native `recall` / `remember` **tools** | `tool` map (`index.ts:226`) | Yes |
| Observe tool results → decide what to remember | `tool.execute.after` mutable output (`index.ts:274`) | Yes |
| Observe assistant text → summarize/extract | `experimental.text.complete` (`index.ts:327`) | Yes |
| Observe the user turn → pre-retrieve | `chat.message` (`index.ts:234`) | Yes |
| **Trigger dreaming** on session events (idle, compaction, etc.) | `event` (every EventV2) (`index.ts:224`) | Yes |
| Align dreaming with OpenCode's own compaction | `experimental.session.compacting` (`index.ts:305`) | Yes (bonus) |
| Own a store keyed by a path; talk to siblings | `PluginInput` (`client`, `serverUrl`, `directory`, `worktree`, `$`) (`index.ts:56`) | Yes |

**The one real gap vs. a fork:** a plugin cannot register a *typed* `SystemContext`
source and therefore doesn't get the snapshot/diff "Mid-Conversation System
Message" machinery (see [`03`](03-context-and-compaction.md)). That is an
*efficiency optimization* (emit an update only when memories change), **not a
capability** — `experimental.chat.system.transform` lets us inject the current
memory block on **every** turn regardless. So the memory system is not
compromised; we only forgo a caching nicety.

**Fork fallback (documented, not chosen):** if we later require the diff machinery
(e.g. to hit the `<10%` overhead with very large memory blocks via prompt caching),
move injection into a `SystemContext` source in the fork
(`packages/core/src/system-context/`, modeled on `instruction-context.ts`). The
plugin's store + dreaming code is reusable as-is; only the injection site changes.

> **Cross-language note.** The plugin is TypeScript. Brent's stores/router and
> Scott's dreaming are Python. The plugin either (a) embeds a thin client that
> talks to a **local memory service** (the Python harness components behind a small
> local HTTP/stdio endpoint), or (b) the memory engine is reimplemented in TS
> inside the plugin. Pick per component; the store path/config is the same either
> way. This is a build-time decision for the team, captured as an open question
> in §5.

## 2. How OpenCode is driven (identically for dev and harness)

A developer types prompts into OpenCode. The harness does the same thing
non-interactively: `opencode run --format json --dangerously-skip-permissions
--dir <repo> --model anthropic/claude-haiku-4-5 "<task>"`
(see [`04`](04-build-run-architecture.md)). **No special access.** The harness reads
the NDJSON only to *grade and log* the run (map turns → our `Trajectory`); it never
reaches into OpenCode's memory. Memory happens entirely inside the plugin, exactly
as it would for the human.

The eval-side `OpenCodeAgent.solve` (`eval/memeval/opencode/agent.py`) becomes a
**runner + trajectory recorder**, not a memory orchestrator: spawn `opencode run`,
parse NDJSON, populate `TrajectoryStep`s, return `AgentResult(patch=…)`. The
`MemoryFramework` scaffold's role shrinks accordingly (it is no longer handed to
OpenCode as a `store=`); memory is owned by the plugin. We update those scaffolds
to match — the harness side is ours to change.

## 3. Per-version memory isolation (the test protocol)

Isolation is a **fresh store path per run** — nothing in the memory code knows
about "versions":

```
MEMORY_STORE=/runs/baseline/store.db        # baseline: memory disabled (empty, unused)
MEMORY_STORE=/runs/v1.0/store.db            # memory v1, fresh
MEMORY_STORE=/runs/v1.1/store.db            # next version, fresh & empty
```

The plugin reads its store location from config/env (set by the dev, or by the
harness per run). vN+1 simply points at a new empty path, so it **cannot** build on
vN. The same mechanism lets a developer keep a persistent personal store by pointing
at a stable path.

### The four run shapes (5 suites, N tasks each)

| Run | Memory | Dreaming | Shape | Store |
|---|---|---|---|---|
| **Baseline** | off | off | 5 × N | none (memory disabled) |
| **Memory v1** | on | off | 5 × N | fresh per run |
| **Dreaming baseline** | on | on, **interleaved** | per suite: run N → dream → run next N → dream → … to 5×N | fresh per run |
| **Full vX.Y** | on | on | (N + Dream) × 5 | fresh per run |

Dreaming is invoked **between task batches**, not by a harness backdoor: the
plugin's `event` hook (or an explicit "dream now" signal the dev could also trigger)
runs consolidation on the current store. The harness sequences batches and triggers
the dream pass the same way a developer could between work sessions — i.e. a normal,
public action, not a private hook.

## 4. Mapping OpenCode signals → our schema (grading/logging only)

The harness consumes OpenCode's **public** output to grade and log — never to do
memory. Memory tool calls show up in the stream because the model called them, just
like any other tool.

| OpenCode signal (public) | Source | Harness target |
|---|---|---|
| `step-finish.tokens.{input,output}` | NDJSON / SSE | `TrajectoryStep(kind="generate").tokens_in/out` |
| `tool` part (call + result) | NDJSON / SSE | `TrajectoryStep(kind="note")`; if it's `recall`/`remember`, also `retrieve`/`write` |
| final assistant text / patch | NDJSON / `GET /session/:id/message` | `AgentResult.prediction` / `.patch` |
| `step-finish.cost` | NDJSON | cross-check `CostTracker` |
| `session.status == idle` | NDJSON / SSE | end of `solve` |

**Efficiency metric caveat:** OpenCode's token count is char/4
([`03`](03-context-and-compaction.md) §4), not a real tokenizer. For the memory
**overhead** metric, the *plugin* should report the memory block's token cost (so
`RetrievedItem.tokens` is authoritative and consistent across runs), rather than
inferring it from OpenCode's heuristic.

## 5. Open questions for the team

1. **Plugin memory engine: embed Python service vs. reimplement in TS.** Brent's
   stores/router + Scott's dreaming are Python. Does the plugin call them over a
   local endpoint, or do we port the engine to TS? Affects who owns what. **Bar:
   whichever keeps the memory system uncompromised.**
2. **Tools vs. forced injection as the headline policy.** `recall`/`remember`
   tools (model decides) vs. `experimental.chat.system.transform` (always inject).
   Both are available; measure both — the comparison is itself a result.
3. **Dream-trigger signal that's public.** Confirm the plugin `event` hook fires on
   a signal we can drive between batches (idle/compaction) so the harness sequences
   dreaming without a private API. If not, define a public "dream" action a dev
   could also use.
4. **`as_of` for coding benchmarks.** No natural query_time;
   `_task_query_time` (`agent.py:519`) falls back to latest session ts. Confirm
   SWE-Bench-CL `group_id`/`order` drives continual-learning ordering.

## 6. TL;DR

Build the memory framework as a **distributable OpenCode plugin** so a real
developer gets it natively (fork only as a documented fallback if we ever need the
`SystemContext` diff/caching machinery — the plugin's hook surface otherwise
matches a fork). The **test harness drives OpenCode exactly as a human would**
(`opencode run`), reading only public output to grade and log — it never touches
memory directly. **Per-version isolation** is a fresh store path per run, so each
framework version earns its own memories from empty. The four run shapes (baseline,
memory v1, interleaved dreaming baseline, full vX.Y over the 5 suites × N) are
sequenced by the harness using only public OpenCode actions.
