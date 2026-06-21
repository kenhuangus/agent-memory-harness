"""Cookbook Memory — persistent memory for coding agents.

A harness-agnostic memory plugin: a shared :mod:`cookbook_memory.core` (the
Orchestrator seam, recall/remember, the events stream) plus per-harness adapters
under :mod:`cookbook_memory.adapters` (Claude Code first). The plugin is a *client*
of the Orchestrator (route · rank · dedup) and owns no store, dreaming, or eval
logic — those are separate workstreams it calls into.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
