"""Path + session resolution from the environment (store-by-path, ADR-storage-001).

Every client (the MCP server, the ``memory`` CLI, the hooks) locates its store and
its events stream the same way: from ``$MEMORY_STORE``. Centralizing it here keeps
the convention in one place and makes the events path deterministic across processes
sharing a store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Default events filename under the store dir (ADR-harness-007).
EVENTS_FILENAME = "events.jsonl"


@dataclass(slots=True)
class Settings:
    """Resolved runtime settings for one client invocation."""

    store_path: Optional[Path]
    events_path: Optional[Path]
    session_id: Optional[str]
    default_k: int = 5

    @classmethod
    def from_env(
        cls,
        env: Optional[dict[str, str]] = None,
        *,
        store: Optional[str] = None,
        session_id: Optional[str] = None,
        k: Optional[int] = None,
    ) -> "Settings":
        """Build settings from the environment, with explicit overrides winning.

        ``store`` overrides ``$MEMORY_STORE``; ``session_id`` overrides
        ``$CLAUDE_SESSION_ID``; ``k`` overrides ``$MEMORY_K`` (default 5). When no
        store is resolvable, ``store_path``/``events_path`` are ``None`` — the
        fail-open path (recall empty, remember no-op).
        """
        env = os.environ if env is None else env
        raw_store = store or env.get("MEMORY_STORE")
        store_path = Path(raw_store) if raw_store else None
        events_path = store_path / EVENTS_FILENAME if store_path else None
        sid = session_id or env.get("CLAUDE_SESSION_ID") or env.get("MEMORY_SESSION_ID")
        if k is not None:
            kk = k
        else:
            try:
                kk = int(env.get("MEMORY_K", "5"))
            except ValueError:
                kk = 5
        return cls(store_path=store_path, events_path=events_path, session_id=sid, default_k=kk)


__all__ = ["Settings", "EVENTS_FILENAME"]
