---
name: remember
description: >-
  Save a durable note to the agent's persistent memory so it survives across turns
  and sessions — a decision and its rationale, a project fact or constraint, a
  convention to follow, or a correction the user made. Use when the user says to
  remember something, or when you learn something non-obvious that a future session
  would need and couldn't re-derive from the code or history.
---

# Remember to persistent memory

Use the `remember` MCP tool (`mcp__cookbook-memory__remember`) to persist a note for
future turns and sessions.

- Call `remember(content, tags)`. Write `content` as a single self-contained fact or
  decision that will still make sense out of context later — include the *why* for a
  decision, the concrete value for a fact. Add `tags` (e.g. `["decision", "auth"]`)
  to aid later retrieval.
- It returns the new memory's `id` (empty if memory is unavailable — fail-open; don't
  treat that as an error).
- Save the **non-obvious and durable**: decisions + rationale, constraints,
  conventions, corrections. Don't save what's already in the code, the git history,
  or this conversation alone.

Pair with the `recall` skill: recall relevant memory before acting, remember what's
new and worth keeping after.
