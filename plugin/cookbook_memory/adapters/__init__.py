"""Per-harness adapters over the shared :mod:`cookbook_memory.core`.

Each adapter is thin — config plus a few hook shims for one coding harness — because
the recall/remember/events logic lives once in the core. Harness-specific code lives
only here; everything reusable belongs in the core.
"""
