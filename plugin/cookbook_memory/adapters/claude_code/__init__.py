"""Claude Code adapter: the bundled plugin (MCP server + hooks + skills).

A thin wrapper over :mod:`cookbook_memory.core`. The MCP server exposes ``recall`` as
a model-callable tool; the hooks observe the session lifecycle (fail-open); the
skills (generic, from the core) give the human-facing affordances. All of it goes
through a ``MemoryClient`` from the core (ADR-harness-001/002).
"""
