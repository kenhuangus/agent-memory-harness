"""SQLite FTS5 lexical backend implementing ``MemoryStore``.

The FTS5 table is an explicit secondary index over this store's durable ``items``
table. Writes update both in the same SQLite transaction; Markdown/OKF remains a
separate source of truth and is not touched here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterator, Optional

from ..harness import _bm25_scores, _tokenize
from ..schema import MemoryItem, RetrievedItem

FTS5_RANKING_NATIVE = "native"
FTS5_RANKING_SHARED = "shared"


def _estimate_tokens(content: str) -> int:
    try:
        from ..models import estimate_tokens
        return estimate_tokens(content)
    except Exception:
        return max(1, len(content or "") // 4)


class Fts5Store:
    """SQLite-backed lexical ``MemoryStore`` using the stdlib FTS5 extension."""

    def __init__(
        self,
        path: str = ":memory:",
        *,
        ranking: str = FTS5_RANKING_NATIVE,
    ) -> None:
        if ranking not in (FTS5_RANKING_NATIVE, FTS5_RANKING_SHARED):
            raise ValueError(f"unknown ranking {ranking!r}")
        self.path = str(path)
        self.ranking = ranking
        self.lexical_index_status = "fts5 unavailable"
        self._fts5_enabled = False
        self._lock = threading.Lock()
        self._closed = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() not in ("wal", "memory"):
            raise RuntimeError(
                f"Fts5Store requires WAL for a file-backed DB; "
                f"got journal_mode={mode!r} for path {self.path!r}"
            )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            "item_id TEXT PRIMARY KEY, content TEXT, timestamp REAL, relevancy REAL, "
            "session_id TEXT, source TEXT, tags TEXT, tokens INTEGER, version INTEGER, "
            "metadata TEXT)"
        )
        self._try_enable_fts5()
        self._conn.commit()

    # -- MemoryStore protocol ----------------------------------------------
    def write(self, item: MemoryItem) -> None:
        tokens = item.tokens
        if tokens <= 0:
            tokens = _estimate_tokens(item.content)
        with self._lock:
            self._raise_if_closed()
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO items (item_id, content, timestamp, relevancy, "
                    "session_id, source, tags, tokens, version, metadata) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        item.item_id,
                        item.content,
                        item.timestamp,
                        item.relevancy,
                        item.session_id,
                        item.source,
                        json.dumps(list(item.tags)),
                        tokens,
                        item.version,
                        json.dumps(item.metadata or {}),
                    ),
                )
                if self._fts5_enabled:
                    self._upsert_fts5_unlocked(item.item_id, item.content)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get(self, item_id: str) -> Optional[MemoryItem]:
        with self._lock:
            self._raise_if_closed()
            row = self._conn.execute(
                f"SELECT {_ITEM_COLS} FROM items WHERE item_id = ?", (item_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        as_of: Optional[float] = None,
        **kwargs: Any,
    ) -> list[RetrievedItem]:
        with self._lock:
            self._raise_if_closed()
        q = _tokenize(query or "")
        if not q or k <= 0 or not self._fts5_enabled:
            return []
        match = " OR ".join('"%s"' % token for token in q)
        rows = self._search_rows(match, as_of=as_of)
        items = [self._row_to_item(row) for row in rows]
        if self.ranking == FTS5_RANKING_SHARED:
            return self._rank_shared(query, items, k=k)
        scored = [
            (-float(row["bm25_raw"]), item)
            for row, item in zip(rows, items)
        ]
        scored.sort(key=lambda si: (-si[0], -si[1].relevancy, -si[1].timestamp, si[1].item_id))
        return [
            RetrievedItem(item=item, score=score, rank=rank)
            for rank, (score, item) in enumerate(scored[: max(0, k)])
        ]

    def all(self) -> list[MemoryItem]:
        with self._lock:
            self._raise_if_closed()
            rows = self._conn.execute(
                f"SELECT {_ITEM_COLS} FROM items ORDER BY rowid"
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def iter_pages(self, *, page_size: int = 1000) -> Iterator[list[MemoryItem]]:
        """ADR-dreaming-028 §2 PR #2g — stream items in pages of up to
        ``page_size``. Avoids materializing the full ``items`` table in
        Python (today's ``all()`` pattern) on large stores.

        Implementation uses ``cursor.fetchmany(page_size)`` over a single
        rowid-ordered SELECT so SQLite never has to seek; rows arrive in
        the same order ``all()`` returns. The store's ``self._lock`` is
        held for the duration of iteration — consumers should not call
        other store operations from inside the loop. The dream worker
        runs under a basedir flock so no concurrent dream invocations
        compete; within a single process the lock serializes connection
        access the same way ``all()`` already does.
        """
        if page_size <= 0:
            raise ValueError(f"page_size must be > 0, got {page_size}")
        with self._lock:
            self._raise_if_closed()
            cursor = self._conn.execute(
                f"SELECT {_ITEM_COLS} FROM items ORDER BY rowid"
            )
            while True:
                rows = cursor.fetchmany(page_size)
                if not rows:
                    break
                yield [self._row_to_item(r) for r in rows]

    def delete(self, item_id: str) -> bool:
        with self._lock:
            self._raise_if_closed()
            try:
                fts_rowid = None
                if self._fts5_enabled:
                    row = self._conn.execute(
                        "SELECT fts_rowid FROM items_fts_ids WHERE item_id = ?",
                        (item_id,),
                    ).fetchone()
                    fts_rowid = row["fts_rowid"] if row else None
                cur = self._conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
                if cur.rowcount > 0 and self._fts5_enabled:
                    if fts_rowid is not None:
                        self._conn.execute(
                            "DELETE FROM items_fts WHERE rowid = ?", (fts_rowid,)
                        )
                    self._conn.execute(
                        "DELETE FROM items_fts_ids WHERE item_id = ?", (item_id,)
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return cur.rowcount > 0

    # -- helpers -----------------------------------------------------------
    def _try_enable_fts5(self) -> None:
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts "
                "USING fts5(content, tokenize='unicode61')"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS items_fts_ids ("
                "item_id TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL UNIQUE)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS fts_rowid_seq ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), next_rowid INTEGER NOT NULL)"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO fts_rowid_seq (id, next_rowid) VALUES (1, 1)"
            )
            self._rebuild_fts5_if_needed_unlocked()
            self._fts5_enabled = True
            self.lexical_index_status = "fts5 active"
        except Exception as exc:
            self._conn.rollback()
            self._fts5_enabled = False
            self.lexical_index_status = f"fts5 unavailable: {exc}"

    def _search_rows(self, match: str, *, as_of: Optional[float]) -> list:
        with self._lock:
            self._raise_if_closed()
            if as_of is None:
                return self._conn.execute(
                    f"""
                    SELECT {_ITEM_COLS_I}, bm25(items_fts) AS bm25_raw
                    FROM items_fts
                    JOIN items_fts_ids m ON m.fts_rowid = items_fts.rowid
                    JOIN items i ON i.item_id = m.item_id
                    WHERE items_fts MATCH ?
                    """,
                    (match,),
                ).fetchall()
            return self._conn.execute(
                f"""
                SELECT {_ITEM_COLS_I}, bm25(items_fts) AS bm25_raw
                FROM items_fts
                JOIN items_fts_ids m ON m.fts_rowid = items_fts.rowid
                JOIN items i ON i.item_id = m.item_id
                WHERE items_fts MATCH ? AND i.timestamp <= ?
                """,
                (match, as_of),
            ).fetchall()

    def _rank_shared(self, query: str, candidates: list[MemoryItem], *, k: int) -> list[RetrievedItem]:
        bm25 = _bm25_scores(query, [(it.item_id, it.content) for it in candidates])

        def sort_key(it: MemoryItem) -> tuple[float, float, float, float, str]:
            score, cover = bm25[it.item_id]
            return (-score, -cover, -it.relevancy, -it.timestamp, it.item_id)

        candidates.sort(key=sort_key)
        return [
            RetrievedItem(item=item, score=bm25[item.item_id][0], rank=rank)
            for rank, item in enumerate(candidates[: max(0, k)])
        ]

    def _next_fts_rowid_unlocked(self) -> int:
        row = self._conn.execute(
            "SELECT next_rowid FROM fts_rowid_seq WHERE id = 1"
        ).fetchone()
        fts_rowid = int(row["next_rowid"])
        self._conn.execute(
            "UPDATE fts_rowid_seq SET next_rowid = ? WHERE id = 1",
            (fts_rowid + 1,),
        )
        return fts_rowid

    def _upsert_fts5_unlocked(self, item_id: str, content: str) -> None:
        row = self._conn.execute(
            "SELECT fts_rowid FROM items_fts_ids WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row:
            fts_rowid = int(row["fts_rowid"])
        else:
            fts_rowid = self._next_fts_rowid_unlocked()
            self._conn.execute(
                "INSERT INTO items_fts_ids (item_id, fts_rowid) VALUES (?, ?)",
                (item_id, fts_rowid),
            )
        self._conn.execute("DELETE FROM items_fts WHERE rowid = ?", (fts_rowid,))
        self._conn.execute(
            "INSERT INTO items_fts(rowid, content) VALUES (?, ?)",
            (fts_rowid, content),
        )

    def _rebuild_fts5_if_needed_unlocked(self) -> None:
        item_count = self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        map_count = self._conn.execute("SELECT COUNT(*) FROM items_fts_ids").fetchone()[0]
        fts_count = self._conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
        if item_count == map_count == fts_count:
            return
        self._conn.execute("DELETE FROM items_fts")
        self._conn.execute("DELETE FROM items_fts_ids")
        self._conn.execute("UPDATE fts_rowid_seq SET next_rowid = 1 WHERE id = 1")
        rows = self._conn.execute(
            "SELECT item_id, content FROM items ORDER BY rowid"
        ).fetchall()
        for row in rows:
            self._upsert_fts5_unlocked(row["item_id"], row["content"])

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError("operation on a closed Fts5Store")

    def _row_to_item(self, row) -> MemoryItem:
        return MemoryItem(
            item_id=row["item_id"],
            content=row["content"],
            timestamp=row["timestamp"],
            relevancy=row["relevancy"],
            session_id=row["session_id"],
            source=row["source"],
            tags=json.loads(row["tags"] or "[]"),
            tokens=row["tokens"],
            version=row["version"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def __enter__(self) -> "Fts5Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


_ITEM_COLS = (
    "item_id, content, timestamp, relevancy, session_id, source, "
    "tags, tokens, version, metadata"
)
_ITEM_COLS_I = (
    "i.item_id, i.content, i.timestamp, i.relevancy, i.session_id, i.source, "
    "i.tags, i.tokens, i.version, i.metadata"
)

__all__ = [
    "FTS5_RANKING_NATIVE",
    "FTS5_RANKING_SHARED",
    "Fts5Store",
]
