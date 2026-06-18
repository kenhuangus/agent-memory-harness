# Cross-Harness Memory Integration

Whether one memory-plugin **core** can serve the popular coding harnesses behind
thin per-harness **adapters**, and what each harness exposes. Owner: **P1 Keith**.

## Read in this order

1. **[01 — Cross-harness comparison](01-cross-harness-comparison.md)** — the
   capability matrix across OpenCode / Claude Code / Codex, and the **one-core +
   three-adapters** recommendation. Start here.
2. **[02 — Claude Code](02-claude-code.md)** — plugin (MCP + hooks), the
   no-per-model-call-injection gap, headless `claude -p`.
3. **[03 — Codex CLI](03-codex.md)** — MCP + `Stop`/`notify`, no session-end / no
   background jobs (the design floor), headless `codex exec --json`.

For the full OpenCode internals (the reference harness), see
[`../opencode/`](../opencode/).

## Takeaway

**Yes — one memory core can serve all three.** The model-callable **MCP tool**
(`recall`/`remember`) is supported identically everywhere and carries the heavy
logic (store, router, retrieval, dreaming), with the per-run store path
(`MEMORY_STORE`) giving version isolation for free. Adapters differ only in
*supplementary* forced injection (full per-turn in OpenCode; per-prompt in Claude
Code / Codex) and the *signal* that triggers dreaming (`event` / `SessionEnd` /
`Stop`+self-background). Design the core to the **Codex floor** — model-pulled
retrieval, `Stop`+`PreCompact` dreaming, store-by-path isolation — and each
harness's richer hooks become optional enhancements. That makes the memory
framework a genuine, distributable contribution, not a one-harness artifact.
