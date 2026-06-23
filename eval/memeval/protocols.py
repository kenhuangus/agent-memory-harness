"""Structural interfaces (``typing.Protocol``) for the memory harness.

These are the seams every workstream builds against. Because they are
:class:`typing.Protocol` (structural / duck-typed), an implementation does NOT
need to subclass them -- it only needs matching method signatures. That lets
Brent's three storage backends, Ken's model adapters, and the four loaders be
written independently and still satisfy the frozen contract.

Standard-library only; safe to import on Python 3.11+.

Three protocols
---------------
MemoryStore   write / get / search / all  -- the persistence + retrieval seam.
ModelAdapter  generate(...) -> (text, tokens_in, tokens_out) + name/price keys.
Loader        load(path_or_id, **) -> list[Task]  -- one per benchmark.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from .schema import MemoryItem, RetrievedItem, Task


@runtime_checkable
class MemoryStore(Protocol):
    """A pluggable memory backend (markdown / sqlite-vector / graph).

    The offline harness ships an in-memory reference implementation
    (``memeval.harness.InMemoryStore``) that satisfies this protocol with the
    standard library only. Real backends (Brent's adapters) implement the same
    four methods. ``search`` MUST return items sorted by descending score with
    ``rank`` set (0 == best), and MUST set ``RetrievedItem.tokens`` via the
    underlying ``MemoryItem.tokens`` so the efficiency metric can be computed.
    """

    def write(self, item: MemoryItem) -> None:
        """Persist a single memory item (idempotent on ``item.item_id``)."""
        ...

    def get(self, item_id: str) -> Optional[MemoryItem]:
        """Return the item with this id, or ``None`` if absent."""
        ...

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        as_of: Optional[float] = None,
        **kwargs: Any,
    ) -> list[RetrievedItem]:
        """Return the top-``k`` items for ``query``, best-ranked first.

        ``as_of`` optionally restricts results to items with
        ``timestamp <= as_of`` (no peeking at the future); ``None`` disables
        the filter. Implementations may accept extra backend-specific kwargs.
        """
        ...

    def all(self) -> list[MemoryItem]:
        """Return every stored item (order unspecified). Used by dreaming."""
        ...

    def delete(self, item_id: str) -> bool:
        """Remove the item with this id; return ``True`` if it was present, else ``False``.

        Idempotent: deleting an absent id is a no-op that returns ``False`` (never raises). The
        retention/version primitive (ADR-P9; version-highest-wins) the persistence layer builds on.
        """
        ...


@runtime_checkable
class ModelAdapter(Protocol):
    """A text-generation model behind a uniform call signature.

    Implementations: ``EchoModel`` (offline, deterministic, no network) and
    ``AnthropicAdapter`` (lazy-imports ``anthropic``). ``generate`` returns the
    completion text together with input/output token counts so the harness can
    feed the CostTracker without a second tokenizer pass.

    The ``name``, ``price_in`` and ``price_out`` attributes let the harness
    build a :class:`~memeval.schema.ModelConfig` and price a run; prices are
    USD per *million* tokens (consistent with ``cost.PRICING``).
    """

    name: str
    price_in: float
    price_out: float

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> tuple[str, int, int]:
        """Return ``(text, tokens_in, tokens_out)`` for ``prompt``.

        Must be deterministic when ``temperature == 0`` for the offline adapter
        so smoke tests are stable. ``tokens_in``/``tokens_out`` are the actual
        billed counts (or a stdlib estimate for the offline adapter).
        """
        ...


@runtime_checkable
class Loader(Protocol):
    """Turns a benchmark source into a normalized ``list[Task]``.

    One implementation per benchmark, registered in
    ``memeval.loaders.get_loader``. ``load`` accepts either a local path (JSON
    file or fixture) or a remote dataset id; offline parsing of a local path /
    fixture MUST work with the standard library only -- any network/`datasets`
    code is lazily imported and reached only for the remote id path.
    """

    #: The benchmark this loader produces tasks for.
    benchmark: Any  # schema.Benchmark; typed Any to avoid an import cycle hazard

    #: Default remote source (HF dataset id, repo, or URL) baked in per loader.
    default_source: str

    def load(
        self,
        path_or_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
        split: str = "test",
        **kwargs: Any,
    ) -> list[Task]:
        """Return normalized tasks.

        If ``path_or_id`` points at an existing local file, parse it offline
        (stdlib only). Otherwise treat it (or ``default_source``) as a remote
        id and lazily import the heavy deps needed to fetch it. ``limit`` caps
        the number of tasks returned (cheap dev iteration).
        """
        ...


__all__ = ["MemoryStore", "ModelAdapter", "Loader"]
