# OpenCode Integration Docs

How OpenCode works internally and how Cookbook Memory plugs into it. Produced by
exploratory research over the `../opencode` fork (TypeScript / Bun, `dev` branch),
cross-referenced against our frozen contract (`eval/memeval/protocols.py`,
`schema.py`, `architecture.md`). Owner: **P1 Keith**.

> **OpenCode is the reference adapter** of a portable, multi-harness memory core.
> For the cross-harness picture (Claude Code + Codex) and the one-core/many-adapters
> architecture, see [`../harnesses/`](../harnesses/01-cross-harness-comparison.md).

## Read in this order

1. **[01 — Agent loop](01-agent-loop.md)** — the `runLoop`/`processor`/`llm`
   pipeline, how a turn is issued, tools, and the six concrete **hook points** for
   memory.
2. **[02 — Extension surfaces](02-extension-surfaces.md)** — plugins, event bus,
   HTTP/SSE API, custom tools, MCP, config. Ranked for a **cross-language (Python)**
   memory layer.
3. **[03 — Context & compaction](03-context-and-compaction.md)** — System Context
   Registry, AGENTS.md loading, prompt assembly order, token budget, compaction.
   Where injected memory should live; **compaction ≈ dreaming**, **AGENTS.md ≈
   memory injection**.
4. **[04 — Build / run / architecture](04-build-run-architecture.md)** — package
   map, build commands, **headless mode** (`opencode run --format json`,
   `opencode serve`), model/key config, trajectory capture.
5. **[05 — Integration strategy](05-integration-strategy.md)** — the synthesis: how
   the OpenCode loop maps onto `AgentAdapter`/`MemoryStore`, and a **phased plan**
   (drive → MCP memory tools → SSE dreaming → optional TS injection shim).

## One-paragraph takeaway

OpenCode's native loop is exactly `retrieve → generate → tool → remember`, and it
exposes clean hook points (`experimental.chat.system.transform`,
`tool.execute.after`, `experimental.text.complete`, `event`) plus a full HTTP/SSE
API and MCP client. It has **no existing cross-session memory** — AGENTS.md on disk
is the only analog. Two design constraints decide the architecture: memory must be
a **native OpenCode capability a real developer gets for free** (not a harness
backdoor), and memory must be **isolated per framework version** (each version
earns its own memories from an empty store). So we build the memory framework as a
**distributable OpenCode plugin** whose hook surface matches a true forked
integration (fork held in reserve only for the prompt-cache diff machinery a plugin
lacks). The **test harness drives OpenCode exactly as a user would**
(`opencode run`), reading only public output to grade and log — never touching
memory directly — and enforces per-version isolation with a **fresh store path per
run**.

## Caveats

- Line numbers reference the fork at research time and may drift; treat them as
  anchors, verify before editing OpenCode.
- These docs are a **design proposal**, not part of the frozen contract. The
  contract lives in [`../../architecture.md`](../../architecture.md) and the
  `eval/memeval/` protocol modules.
