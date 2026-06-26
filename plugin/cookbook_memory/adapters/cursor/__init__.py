"""Cursor CLI adapter for the cookbook-memory plugin.

The sibling of :mod:`cookbook_memory.adapters.claude_code`. Same harness-agnostic
core (the ``recall`` MCP server + the Daydreamer write path); only the harness
plumbing differs:

* the model-callable ``recall`` tool ships via the plugin **bundle** loaded with
  ``cursor-agent --plugin-dir`` (Cursor's only install path — no ``plugin install``
  subcommand). ``mcp.json`` lives at the bundle ROOT (verified: that is where the
  loader reads it; under ``.cursor/`` it does not load).
* the Daydreamer is fired by the **``sessionEnd``** hook (Cursor's per-turn /
  turn-complete analog of Claude Code's ``Stop``), with ``preCompact`` as the
  pre-compaction trigger. Verified: a ``sessionEnd`` hook FIRES in headless
  ``cursor-agent --print`` and its stdin payload carries ``transcript_path`` +
  ``session_id``.  NOTE: plugin-*bundled* hooks do NOT fire headless — the harness
  therefore installs these hooks at user level (``$HOME/.cursor/hooks.json``) for
  eval runs; the bundle copy is for interactive/IDE use.

See [`docs/harnesses/06-cursor-cli.md`](../../../../docs/harnesses/06-cursor-cli.md)
and ADR-harness-013/014/015.
"""
