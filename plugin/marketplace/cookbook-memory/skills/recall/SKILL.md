---
name: recall
description: >-
  Search the agent's persistent memory for notes relevant to the current task —
  prior decisions, facts, context, or conventions saved in earlier turns or
  sessions. Use when the user refers to something decided or learned before
  ("what did we decide about X", "remember the Y config", "how did we do Z last
  time"), or when prior context would help and isn't in the current conversation.
---

# Recall from persistent memory

Use the `recall` memory tool to retrieve relevant memories before answering, whenever
the task could benefit from something decided or learned earlier. (The tool is
exposed by the memory plugin's MCP server; the exact tool id is harness-specific.)

- Call `recall(query, k)` with a focused natural-language query describing what you
  need. `k` defaults to 5; raise it when you want broader context.
- Each hit has `id`, `content`, `score` (higher = more relevant), and `tokens`.
  Hits come back ranked, best first.
- Fold the relevant hits into your reasoning; cite what you used. If recall returns
  nothing, proceed without it — memory is supplementary, never required (the system
  is fail-open).

The conscious agent is recall-only — saving memories happens automatically in the
background (the Daydreamer watches the session), so there is no remember tool to call.
