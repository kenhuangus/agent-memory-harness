"""MemoryService — the engine behind our Claude Code memory plugin.

Wraps a :class:`~memeval.protocols.MemoryStore` with two agent-facing operations,
``recall`` and ``remember``, and logs every recall so the benchmark harness can
reconstruct what the agent retrieved (and score recency / relevancy / efficiency)
even though retrieval happened inside the Claude Code process.

Pure and stdlib-only: the MCP server (:mod:`memeval.claudecode.memory_server`)
and the offline tests both use this directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..protocols import MemoryStore
from ..schema import MemoryItem


class MemoryService:
    """Recall/remember over a MemoryStore, with a JSONL retrieval log.

    ``store`` is any MemoryStore (default: an OKF-backed store, so the plugin's
    memory is a portable OKF bundle on disk). ``log_path`` receives one JSON line
    per ``recall`` — the benchmark agent reads it back to attribute retrievals to
    the trajectory. ``seed`` pre-loads the store (the task's prior sessions).
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        log_path: Optional[str | Path] = None,
        default_k: int = 5,
    ) -> None:
        self.store = store
        self.log_path = Path(log_path) if log_path else None
        self.default_k = default_k

    # -- agent-facing tools ------------------------------------------------- #
    def recall(self, query: str, k: Optional[int] = None, *, as_of: Optional[float] = None) -> list[dict[str, Any]]:
        """Search memory; return compact hit dicts and append them to the log.

        Each hit: ``{id, content, score, rank, tokens, timestamp}`` — enough for
        both the agent's prompt and the harness's metric attribution.
        """
        kk = self.default_k if k is None else k
        hits = self.store.search(query, k=kk, as_of=as_of)
        out = [
            {
                "id": h.item_id,
                "content": h.item.content,
                "score": round(float(h.score), 6),
                "rank": h.rank,
                "tokens": h.tokens,
                "timestamp": h.item.timestamp,
            }
            for h in hits
        ]
        self._log({"op": "recall", "query": query, "k": kk, "hits": out})
        return out

    def remember(self, content: str, *, tags: Optional[list[str]] = None,
                 item_id: Optional[str] = None, timestamp: Optional[float] = None,
                 relevancy: float = 1.0) -> str:
        """Write a new memory; return its id. Logged as a ``remember`` op."""
        n = self._count
        iid = item_id or f"cc-mem-{n}"
        self.store.write(MemoryItem(
            item_id=iid, content=content, tags=list(tags or []),
            timestamp=timestamp or 0.0, relevancy=relevancy, source="claude-code",
        ))
        self._count = n + 1
        self._log({"op": "remember", "id": iid, "tags": list(tags or [])})
        return iid

    # -- seeding + log ------------------------------------------------------ #
    _count = 0

    def seed_items(self, items: list[MemoryItem]) -> int:
        """Pre-load memories (e.g. a task's prior sessions). Returns the count."""
        for it in items:
            self.store.write(it)
        return len(items)

    def _log(self, record: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    @staticmethod
    def read_log(path: str | Path) -> list[dict[str, Any]]:
        """Read a retrieval log written by :meth:`recall` (empty if absent)."""
        p = Path(path)
        if not p.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out


__all__ = ["MemoryService"]
