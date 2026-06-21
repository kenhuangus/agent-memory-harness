"""The MemoryClient — the plugin's handle for recall and remember.

A :class:`MemoryClient` is constructed once per process (the MCP server, a CLI
invocation). It builds the memory **engine** over ``$MEMORY_STORE`` once — Brent's
``Router`` plus the store backends, pulled in through the contract seam — and exposes
the plugin's whole memory surface: ``recall`` and ``remember``. Each call:

1. performs the operation (route · rank for recall; write for remember),
2. emits a structured event (ADR-harness-007), and
3. fails open — any error degrades to a safe default (recall → empty, remember →
   empty id) and is recorded as an ``error`` event rather than raised
   (ADR-harness-006), so a memory failure never breaks the caller's turn.

The conscious agent is recall-only (the model reads via the MCP ``recall`` tool); all
memory creation is the Daydreamer's, asynchronously. ``remember`` here backs the
human-facing ``memory-cli remember`` debug command.

The engine is built by :func:`build_engine` — the single seam a test overrides to
inject a fake instead of the real ``Router``+stores.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .events import EventStream


@dataclass(slots=True)
class Hit:
    """One recalled memory, flattened for an adapter/tool response.

    ``id``, ``content``, ``score`` (higher = more relevant), ``tokens``, and the
    0-based ``rank``. Kept separate from the engine's ``RetrievedItem`` so the
    plugin's public response shape doesn't depend on an internal type.
    """

    id: str
    content: str
    score: float
    tokens: int
    rank: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "content": self.content,
            "score": self.score,
            "tokens": self.tokens,
            "rank": self.rank,
        }


class _Engine:
    """The memory engine over a store path: Brent's Router plus the backends.

    Constructs the three store backends (vectors/markdown/graph) under the store path
    and a Router that dispatches each query to the one that answers best. ``recall``
    routes + ranks and maps results to :class:`Hit`; ``remember`` writes to the
    markdown backend. All engine pieces are pulled in through the contract seam — the
    plugin holds none of the routing or storage logic itself.
    """

    def __init__(self, store_path: str) -> None:
        from .contract import load_engine

        engine = load_engine()
        root = Path(store_path)
        root.mkdir(parents=True, exist_ok=True)
        self._backends = {
            "vectors": engine["SqliteVectorStore"](str(root / "memory.db")),
            "markdown": engine["MarkdownStore"](root / "markdown"),
            "graph": engine["GraphStore"](),
        }
        self._router = engine["Router"](self._backends)
        self._n = 0

    def recall(self, query: str, *, k: int, as_of: Optional[float]) -> list[Hit]:
        items = self._router.route(query).search(query, k=k, as_of=as_of)
        return [
            Hit(
                id=it.item_id,
                content=it.item.content,
                score=round(float(it.score), 6),
                tokens=it.tokens,
                rank=it.rank,
            )
            for it in items
        ]

    def remember(self, content: str, *, tags: Optional[list[str]], timestamp: float) -> str:
        from .contract import MemoryItem

        self._n += 1
        item_id = f"cbmem-{self._n}"
        self._backends["markdown"].write(MemoryItem(
            item_id=item_id,
            content=content,
            tags=list(tags or []),
            timestamp=timestamp,
            source="cookbook-memory",
        ))
        return item_id


def build_engine(store_path: str) -> _Engine:
    """Construct the memory engine over ``store_path``.

    The single injection seam: a test overrides this to substitute a fake engine,
    avoiding construction of the real Router/stores. Production code never overrides it.
    """
    return _Engine(store_path)


class MemoryClient:
    """The plugin's per-process handle for recall/remember.

    Resolves the store (``store`` arg or ``$MEMORY_STORE``), builds the engine once
    (lazily, on first use), and wires the events stream. When no store is configured
    or the engine can't be built, the client is **inactive**: recall returns empty and
    remember no-ops (still emitting events) — the fail-open path (ADR-harness-006).
    """

    def __init__(
        self,
        *,
        store: Optional[str] = None,
        session_id: Optional[str] = None,
        default_k: int = 5,
        events: Optional[EventStream] = None,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        from .config import Settings

        settings = Settings.from_env(env, store=store, session_id=session_id, k=default_k)
        self._store_path = str(settings.store_path) if settings.store_path else None
        self.session_id = settings.session_id
        self.default_k = settings.default_k
        self.events = events if events is not None else EventStream(settings.events_path)
        self._engine: Optional[_Engine] = None
        self._engine_built = False

    def _engine_or_none(self) -> Optional[_Engine]:
        """Build the engine once; return ``None`` if unavailable (fail-open)."""
        if not self._engine_built:
            self._engine_built = True
            if self._store_path:
                try:
                    self._engine = build_engine(self._store_path)
                except Exception:
                    self._engine = None
        return self._engine

    def recall(
        self,
        query: str,
        k: Optional[int] = None,
        *,
        as_of: Optional[float] = None,
        ts: float = 0.0,
    ) -> list[Hit]:
        """Search memory; return ranked hits (empty on any failure, fail-open)."""
        kk = self.default_k if k is None else k
        engine = self._engine_or_none()
        if engine is None:
            self.events.emit("recall", session_id=self.session_id, query=query, ts=ts, ids=[], k=kk, n=0)
            return []
        try:
            hits = engine.recall(query, k=kk, as_of=as_of)
        except Exception as exc:
            self.events.emit("error", session_id=self.session_id, query=query, ts=ts,
                             op_attempted="recall", error=str(exc))
            return []
        self.events.emit("recall", session_id=self.session_id, query=query, ts=ts,
                         ids=[h.id for h in hits], k=kk, n=len(hits))
        return hits

    def remember(
        self,
        content: str,
        *,
        tags: Optional[list[str]] = None,
        ts: float = 0.0,
    ) -> str:
        """Persist ``content`` and return its memory id ("" on failure, fail-open)."""
        engine = self._engine_or_none()
        if engine is None:
            self.events.emit("remember", session_id=self.session_id, ts=ts, ids=[], tags=list(tags or []))
            return ""
        try:
            mem_id = engine.remember(content, tags=tags, timestamp=ts)
        except Exception as exc:
            self.events.emit("error", session_id=self.session_id, ts=ts,
                             op_attempted="remember", error=str(exc))
            return ""
        self.events.emit("remember", session_id=self.session_id, ts=ts,
                         ids=[mem_id] if mem_id else [], tags=list(tags or []))
        return mem_id


__all__ = ["Hit", "MemoryClient", "build_engine"]
