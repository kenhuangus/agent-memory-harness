---
name: recall
description: >-
  Search persistent memory for relevant notes from past sessions — prior
  decisions, conventions, gotchas, and how similar work was handled. Recall is
  cheap and fail-open, so call it rather than guess: before starting a task,
  editing a file, fixing a failing test, or choosing an approach — and whenever
  the user refers back ("what did we decide about X", "how did we do Z").
---

# Recall from persistent memory

Retrieve relevant memories before you act, whenever past work could bear on the task.
(The tool is exposed by the memory plugin's MCP server; the exact tool id is
harness-specific.)

**Default to recalling.** It's fast and fail-open — a miss costs nothing, a skipped
hit means repeating a mistake or contradicting an earlier decision. Recall at the
start of a task and at each decision point: opening a file, diagnosing a failing
test, picking an approach, hitting something unfamiliar — not only when asked.

- Call `recall(query, k)` with a focused natural-language query. `k` defaults to 5;
  raise it for broader context.
- Hits return ranked (best first), each with `id`, `content`, `score`, `tokens`.
- Fold relevant hits into your reasoning; cite what you used. Empty result? Just
  proceed.

Recall-only: saving happens in the background (the Daydreamer watches the session),
so there's no remember tool.
