"""Daydream engine — the per-Stop-hook entrypoint.

This module owns :func:`daydream`, the single public callable Claude
Code's Stop hook (PR5) invokes after every agent turn. It wires together
the state layer (sidecar I/O + flock + TTL sweep + current-session touch
from :mod:`memeval.dreaming._state`), the redaction layer
(:mod:`memeval.dreaming.redaction`), the LLM-extraction pipeline
(:mod:`memeval.dreaming._extract`), and the events shim
(:mod:`memeval.dreaming.events`) into the cursor-advance ordering
pinned by ADR-dreaming-013.

Invariant: every ``store.write`` for the chunk completes BEFORE the
single atomic ``_write_sidecar_atomic`` call. Any failure aborts
without advancing the cursor; the dedup pass in the orchestrator
absorbs the duplicate next run (ADR-013 §Decision steps 7-8).

Fail-open boundary: every exception inside the per-session lock is
caught here and converted to an exit-0 return plus a
``daydream.chunk_error`` event (ADR-harness-006). The single non-fail
-open exit is :func:`_state.resolve_basedir` (KeyError /
FileNotFoundError / ValueError on a misconfigured ``MEMORY_STORE``);
the PR5 plugin shim is responsible for swallowing those at the
harness boundary.

Heavy deps are lazy-imported: :func:`make_client` is pulled inside the
``if client is None`` branch so ``import memeval.dreaming.engine``
does not load ``httpx`` (halliday F12).
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Callable

from memeval.dreaming._extract import _default_id_gen, extract_memories
from memeval.dreaming._state import (
    RECENT_MEMORY_CAP,
    SidecarState,
    _LockHeld,
    _per_session_lock,
    _sanity_check_cursor,
    _touch_current_session_files,
    _write_audit_fail_open,
    _write_sidecar_atomic,
    audit_path,
    load_sidecar,
    resolve_basedir,
    sidecar_path,
    sweep_old_state,
)
from memeval.dreaming.events import emit, event_context
from memeval.dreaming.llm import LLMClient
from memeval.dreaming.redaction import redact_with_counts
from memeval.protocols import MemoryStore

_logger = logging.getLogger(__name__)

__all__ = ["daydream"]

_FIRST_BYTES_LEN: int = 64


def daydream(
    *,
    session_id: str,
    log_path: Path,
    store: MemoryStore,
    client: LLMClient | None = None,
    basedir: Path | None = None,
    now: float | None = None,
    id_gen: Callable[[], str] | None = None,
) -> None:
    """Run one Daydream chunk-extraction pass for ``session_id``.

    Reads new bytes from ``log_path`` starting at the per-session
    sidecar cursor, redacts them, calls the LLM through
    :func:`extract_memories`, writes each emitted ``MemoryItem`` to
    ``store``, and finally advances the cursor atomically. Never
    raises to the caller except for the documented
    :func:`resolve_basedir` configuration errors — every other failure
    fails open via :func:`emit` + early return without advancing the
    cursor.
    """
    effective_basedir = basedir if basedir is not None else resolve_basedir()
    _touch_current_session_files(effective_basedir, session_id)

    try:
        sweep_old_state(effective_basedir)
    except Exception as exc:
        _logger.warning("sweep_old_state failed: %s", exc)

    effective_id_gen: Callable[[], str] = id_gen if id_gen is not None else _default_id_gen

    if client is None:
        from memeval.dreaming.llm import make_client

        effective_client: LLMClient = make_client()
    else:
        effective_client = client

    with event_context(session_id=session_id, basedir=effective_basedir):
        cursor: int = 0
        try:
            with _per_session_lock(effective_basedir, session_id):
                sidecar_target = sidecar_path(effective_basedir, session_id)
                state = load_sidecar(sidecar_target)
                cursor = _sanity_check_cursor(state["cursor"], log_path)

                with open(log_path, "rb") as fp:
                    fp.seek(cursor)
                    chunk = fp.read()
                    new_cursor = fp.tell()
                    fp.seek(0)
                    first_bytes = fp.read(_FIRST_BYTES_LEN)

                if not chunk.strip():
                    return

                effective_now = now if now is not None else time.time()
                chunk_text = chunk.decode(errors="replace")
                redacted, detected = redact_with_counts(chunk_text)

                _write_audit_fail_open(
                    audit_path(effective_basedir, session_id),
                    chunk_id=cursor,
                    pre=chunk_text,
                    post=str(redacted),
                    detected=detected,
                )
                for plugin_name, count in detected.items():
                    if count > 0:
                        emit(
                            "redaction.chunk",
                            plugin=plugin_name,
                            count=count,
                            chunk_id=cursor,
                        )

                items = extract_memories(
                    redacted,
                    client=effective_client,
                    session_id=session_id,
                    now=effective_now,
                    id_gen=effective_id_gen,
                )
                if items is None:
                    return

                for item in items:
                    store.write(item)
                    emit(
                        "daydream.memory_written",
                        item_id=item.item_id,
                        session_id=session_id,
                        chunk_id=cursor,
                    )

                new_last_summary: str | None = (
                    items[-1].content if items else state.get("last_summary")
                )
                prepended_ids = [item.item_id for item in items] + list(
                    state["recent_memory_ids"]
                )
                new_state = SidecarState(
                    cursor=new_cursor,
                    last_summary=new_last_summary,
                    recent_memory_ids=prepended_ids[:RECENT_MEMORY_CAP],
                    first_bytes_hash=hashlib.sha256(first_bytes).hexdigest(),
                )
                _write_sidecar_atomic(sidecar_target, new_state)
        except _LockHeld:
            return
        except Exception as exc:
            emit(
                "daydream.chunk_error",
                reason=f"{type(exc).__name__}: {exc}",
                chunk_id=cursor,
            )
            return
