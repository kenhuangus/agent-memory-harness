"""Per-harness adapters over the shared :mod:`cookbook_memory.core`.

Claude Code is the first (and only, for the MVP) adapter. OpenCode and Codex
adapters drop in here as siblings later — each is thin (config + a few hook shims),
because the recall/remember/events logic lives once in the core.
"""
