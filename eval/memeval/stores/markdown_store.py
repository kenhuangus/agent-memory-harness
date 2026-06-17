"""Markdown + YAML backend — owner: Brent. Implements ``MemoryStore``.

TODO(brent): store each memory as a markdown file with YAML frontmatter; keep an
inverted keyword index (keyword -> file paths); ``search`` = keyword lookup +
light re-rank. Honor ``as_of`` and set ``RetrievedItem.tokens`` (see the
invariants in ``architecture.md`` / ``protocols.py``). Stdlib-only is fine here.
"""

from __future__ import annotations

from typing import Any, Optional

from ..schema import MemoryItem, RetrievedItem


class MarkdownStore:
    """Markdown/YAML-file MemoryStore with an inverted keyword index. (stub)"""

    def write(self, item: MemoryItem) -> None:
        raise NotImplementedError("MarkdownStore.write — TODO(brent)")

    def get(self, item_id: str) -> Optional[MemoryItem]:
        raise NotImplementedError("MarkdownStore.get — TODO(brent)")

    def search(
        self, query: str, *, k: int = 5, as_of: Optional[float] = None, **kwargs: Any
    ) -> list[RetrievedItem]:
        raise NotImplementedError("MarkdownStore.search — TODO(brent)")

    def all(self) -> list[MemoryItem]:
        raise NotImplementedError("MarkdownStore.all — TODO(brent)")


__all__ = ["MarkdownStore"]
