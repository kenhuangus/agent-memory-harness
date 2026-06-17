"""Shared loader machinery: local-vs-remote dispatch + normalization helpers.

Every benchmark loader is a :class:`BaseLoader` subclass. The base implements
the public :meth:`BaseLoader.load` from the frozen :class:`memeval.protocols.Loader`
protocol and dispatches:

* ``path_or_id`` points at an **existing local file** -> ``_load_local`` (stdlib
  ``json`` only -- the OFFLINE path, no network, no heavy deps).
* otherwise -> ``_load_remote`` (treats the argument, or ``default_source``, as a
  remote dataset id / repo and **lazily** imports ``datasets`` / ``requests``).

Only the standard library is imported at module load time. Subclasses override
``_load_local`` (always) and ``_load_remote`` (best-effort real source code).

Normalization helpers convert raw benchmark JSON into :class:`Session` records
with stable ids, chronological ``index`` order, and a Unix-epoch ``timestamp``
used by the recency metric.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..schema import Benchmark, Session, Task, TaskKind


# --------------------------------------------------------------------------- #
# Stdlib IO helpers
# --------------------------------------------------------------------------- #
def read_json(path: str | Path) -> Any:
    """Read and parse a JSON file with the standard library only.

    Tolerates JSON Lines (one object per line) when the top-level parse fails:
    benchmarks ship both shapes, so we sniff and fall back to per-line parsing.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        rows: list[Any] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        if not rows:
            raise
        return rows


def file_exists(path: Optional[str]) -> bool:
    """True iff ``path`` is a non-empty string naming an existing file."""
    if not path:
        return False
    try:
        return Path(path).is_file()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Timestamp parsing (-> Unix epoch float, UTC) for the recency metric
# --------------------------------------------------------------------------- #
def to_epoch(value: Any, *, default: float = 0.0) -> float:
    """Best-effort parse of a benchmark timestamp into a Unix-epoch float (UTC).

    Accepts: int/float epochs, ISO-8601 strings (``2023-05-20T13:30:00``,
    optionally trailing ``Z``), ``YYYY-MM-DD`` dates, and the
    ``(YYYY/MM/DD (Sat) HH:MM)`` shape LongMemEval uses. Returns ``default`` on
    anything unparseable -- timestamps are best-effort, never load-bearing for
    correctness, only for recency ordering.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return default
    s = value.strip()
    if not s:
        return default
    # Pure numeric string -> epoch.
    try:
        return float(s)
    except ValueError:
        pass
    # LongMemEval style: "2023/05/20 (Sat) 13:30" -> strip the weekday token.
    cleaned = s.replace("Z", "").strip()
    if "(" in cleaned and ")" in cleaned:
        head, _, tail = cleaned.partition("(")
        _, _, after = tail.partition(")")
        cleaned = (head + after).strip()
    fmts = (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    )
    for fmt in fmts:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return default


# --------------------------------------------------------------------------- #
# Session normalization
# --------------------------------------------------------------------------- #
def _coerce_content(raw: Any) -> str:
    """Flatten a raw session payload into a single text blob.

    Handles plain strings, ``{"role":..,"content":..}`` turn dicts, and lists
    of turns (LongMemEval sessions are lists of turn dicts).
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("content", "text", "value", "turns", "messages"):
            if key in raw:
                return _coerce_content(raw[key])
        return json.dumps(raw, ensure_ascii=False)
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for turn in raw:
            if isinstance(turn, dict):
                role = turn.get("role", "")
                body = _coerce_content(
                    turn.get("content", turn.get("text", ""))
                )
                parts.append(f"{role}: {body}" if role else body)
            else:
                parts.append(str(turn))
        return "\n".join(parts)
    return str(raw)


def normalize_session(raw: dict, *, default_index: int = 0) -> Session:
    """Convert one raw benchmark session dict into a :class:`Session`.

    Reads the session id from any of ``session_id``/``id``/``sid``; the body
    from ``content``/``text``/``turns``/``messages``; the time from
    ``timestamp``/``time``/``date`` (parsed via :func:`to_epoch`). ``index``
    preserves chronological order even when timestamps tie or are absent.
    Unknown keys are preserved in ``metadata``.
    """
    sid = (
        raw.get("session_id")
        or raw.get("id")
        or raw.get("sid")
        or f"sess_{default_index}"
    )
    content = _coerce_content(
        raw.get("content")
        if "content" in raw
        else raw.get("text")
        if "text" in raw
        else raw.get("turns")
        if "turns" in raw
        else raw.get("messages", raw)
    )
    ts = to_epoch(
        raw.get("timestamp")
        if "timestamp" in raw
        else raw.get("time")
        if "time" in raw
        else raw.get("date")
    )
    index = int(raw.get("index", default_index))
    role = str(raw.get("role", "user"))
    known = {
        "session_id", "id", "sid", "content", "text", "turns", "messages",
        "timestamp", "time", "date", "index", "role",
    }
    metadata = {k: v for k, v in raw.items() if k not in known}
    return Session(
        session_id=str(sid),
        content=content,
        timestamp=ts,
        index=index,
        role=role,
        metadata=metadata,
    )


def sessions_from_any(raw: Any) -> list[Session]:
    """Normalize a list/dict of raw sessions into ordered :class:`Session`s.

    Accepts a list of session dicts, a dict keyed by session id, or a single
    session. ``index`` is assigned in iteration order when not already present.
    """
    out: list[Session] = []
    if raw is None:
        return out
    if isinstance(raw, dict) and not _looks_like_single_session(raw):
        # dict keyed by session id
        for i, (key, val) in enumerate(raw.items()):
            if isinstance(val, dict):
                val = {"session_id": key, **val}
            else:
                val = {"session_id": key, "content": val}
            out.append(normalize_session(val, default_index=i))
        return out
    if isinstance(raw, (list, tuple)):
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                out.append(normalize_session(item, default_index=i))
            else:
                out.append(
                    normalize_session(
                        {"session_id": f"sess_{i}", "content": item},
                        default_index=i,
                    )
                )
        return out
    # single session
    if isinstance(raw, dict):
        out.append(normalize_session(raw, default_index=0))
    else:
        out.append(
            normalize_session({"content": raw}, default_index=0)
        )
    return out


def _looks_like_single_session(d: dict) -> bool:
    """Heuristic: a dict is one session (not a session map) if it has a body."""
    return any(
        key in d for key in ("content", "text", "turns", "messages")
    )


# --------------------------------------------------------------------------- #
# Base loader
# --------------------------------------------------------------------------- #
class BaseLoader:
    """Reference loader satisfying :class:`memeval.protocols.Loader`.

    Subclasses set the ``benchmark`` and ``default_source`` class attributes
    and implement :meth:`_load_local`. They may override :meth:`_load_remote`
    with real download code (lazy heavy imports). The public :meth:`load`
    dispatches local-file -> offline parse, else -> remote.
    """

    benchmark: Benchmark
    default_source: str = ""
    #: Subclasses default to QA; SWE loaders override to CODE.
    kind: TaskKind = TaskKind.QA

    def load(
        self,
        path_or_id: Optional[str] = None,
        *,
        limit: Optional[int] = None,
        split: str = "test",
        **kwargs: Any,
    ) -> list[Task]:
        """Return normalized tasks from a local file or a remote source.

        If ``path_or_id`` is an existing file, parse it offline (stdlib only).
        Otherwise treat ``path_or_id`` (or ``self.default_source``) as a remote
        dataset id and reach the lazy-import remote path. ``limit`` caps the
        number of tasks returned.
        """
        if file_exists(path_or_id):
            tasks = self._load_local(path_or_id, limit=limit, **kwargs)  # type: ignore[arg-type]
        else:
            source = path_or_id or self.default_source
            tasks = self._load_remote(
                source, limit=limit, split=split, **kwargs
            )
        if limit is not None:
            tasks = tasks[:limit]
        return tasks

    # -- subclass hooks ----------------------------------------------------- #
    def _load_local(
        self, path: str, *, limit: Optional[int] = None, **kwargs: Any
    ) -> list[Task]:
        """Parse a local JSON file into tasks (stdlib only). Subclass impl."""
        raise NotImplementedError

    def _load_remote(
        self,
        source: str,
        *,
        limit: Optional[int] = None,
        split: str = "test",
        **kwargs: Any,
    ) -> list[Task]:
        """Fetch a remote benchmark via ``datasets`` (lazy import).

        Default implementation streams a HuggingFace dataset and feeds each row
        back through the subclass's local row parser. Subclasses with a non-HF
        source (git repo / URL) override this.
        """
        rows = self._fetch_hf_rows(source, split=split, limit=limit, **kwargs)
        return self._rows_to_tasks(rows, limit=limit)

    # -- remote helpers (lazy imports live here) ---------------------------- #
    def _fetch_hf_rows(
        self,
        source: str,
        *,
        split: str = "test",
        limit: Optional[int] = None,
        name: Optional[str] = None,
        **kwargs: Any,
    ) -> list[dict]:
        """Load rows from a HuggingFace dataset. Lazily imports ``datasets``.

        Raises a clear :class:`RuntimeError` if ``datasets`` is not installed --
        keeping the dependency optional for the offline path.
        """
        try:
            from datasets import load_dataset  # type: ignore
        except Exception as exc:  # pragma: no cover - network/optional dep
            raise RuntimeError(
                "Remote loading requires the optional 'datasets' package "
                "(`pip install datasets`). For offline use, pass a local "
                f"JSON path instead. Source was {source!r}."
            ) from exc
        ds = load_dataset(source, name=name, split=split, streaming=True)
        rows: list[dict] = []
        for i, row in enumerate(ds):  # pragma: no cover - network path
            rows.append(dict(row))
            if limit is not None and len(rows) >= limit:
                break
        return rows

    def _rows_to_tasks(
        self, rows: list[dict], *, limit: Optional[int] = None
    ) -> list[Task]:
        """Convert already-fetched raw rows into tasks via the subclass parser.

        Reuses :meth:`_parse_rows` so remote and local share normalization.
        """
        return self._parse_rows(rows, limit=limit)

    def _parse_rows(
        self, rows: list[dict], *, limit: Optional[int] = None
    ) -> list[Task]:
        """Parse a list of raw task rows. Subclasses implement the mapping."""
        raise NotImplementedError

    # -- shared parsing entry point ----------------------------------------- #
    def _read_rows(self, path: str) -> list[dict]:
        """Read a local JSON(L) file and coerce it to a list of row dicts.

        Supports a top-level list, a ``{"data"|"questions"|"tasks": [...]}``
        wrapper, or a dict keyed by task id.
        """
        data = read_json(path)
        return rows_of(data)


def rows_of(data: Any) -> list[dict]:
    """Coerce parsed JSON into a flat list of row dicts.

    Handles a bare list, common wrapper keys, and id-keyed dicts.
    """
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("data", "questions", "tasks", "examples", "instances"):
            val = data.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
        # id-keyed dict of rows
        if data and all(isinstance(v, dict) for v in data.values()):
            out = []
            for key, val in data.items():
                out.append({"id": key, **val})
            return out
    return []


def first_present(d: dict, *keys: str, default: Any = None) -> Any:
    """Return ``d[k]`` for the first present key in ``keys``, else ``default``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


__all__ = [
    "read_json",
    "file_exists",
    "to_epoch",
    "normalize_session",
    "sessions_from_any",
    "rows_of",
    "first_present",
    "BaseLoader",
]
