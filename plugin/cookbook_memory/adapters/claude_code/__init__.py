"""Claude Code adapter: the bundled plugin (MCP server + hooks + skills).

A thin wrapper over :mod:`cookbook_memory.core`. The MCP server exposes
``recall``/``remember`` as model-callable tools; the hooks observe the session
lifecycle (fail-open no-ops in the walking skeleton — the Daydreamer day pass lands
later); the skills give the human-facing affordances. All of it routes through the
Orchestrator via the core (ADR-harness-001/002).
"""
