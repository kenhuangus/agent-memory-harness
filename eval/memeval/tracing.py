"""Optional, lazy **Langfuse** tracing for the recursive agent + dreaming loops.

Verdict from the instrumentation review: keep the ``Trajectory`` JSONL + metrics
as the machine source of truth; add Langfuse as a *mirror* for humans — a nested
trace tree per task (retrieve → generate → tool → write), per-step latency, the
retrieved items inline, the four metrics as **scores**, and benchmark sweeps as
**dataset runs** to diff memory-on/off and Haiku/Opus.

This module is the minimal-coupling shim. It is a **no-op** unless ``langfuse``
is installed AND ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` are set, so the
offline / CI / stdlib-only path is completely untouched (zero third-party import
at module load; every call degrades to a do-nothing handle and never raises).
Mirrors the lazy ``anthropic`` / ``datasets`` pattern used elsewhere.

Enable a real run with::

    pip install langfuse           # or: pip install memeval[langfuse]
    export LANGFUSE_PUBLIC_KEY=pk-...  LANGFUSE_SECRET_KEY=sk-...
    # host: LANGFUSE_HOST (SDK-native) or LANGFUSE_BASE_URL (alias). For
    # Langfuse Cloud US use https://us.cloud.langfuse.com (EU: https://cloud.langfuse.com).

Then ``run_agent`` automatically emits one trace per task with nested step spans
and attaches the four metrics as scores. Nothing to change at call sites.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

_client: Any = None
_TRIED = False

#: Map our trajectory step kinds -> Langfuse observation types.
_AS_TYPE = {
    "retrieve": "retriever",
    "generate": "generation",
    "write": "span",
    "note": "event",
}


def _host() -> Optional[str]:
    """Langfuse host: ``LANGFUSE_HOST`` (SDK-native) or ``LANGFUSE_BASE_URL`` alias."""
    return os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")


def enabled() -> bool:
    """True iff Langfuse keys are present (the cheap gate before importing it)."""
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _get_client() -> Any:
    """Lazily construct the Langfuse client once; ``None`` if unavailable/disabled."""
    global _client, _TRIED
    if _client is not None or _TRIED:
        return _client
    _TRIED = True
    if not enabled():
        return None
    try:  # pragma: no cover - exercised only when langfuse is installed
        from langfuse import Langfuse  # lazy: only when keys are set
        # Pass host explicitly so LANGFUSE_BASE_URL works as well as the
        # SDK-native LANGFUSE_HOST; keys come from the env the SDK already reads.
        host = _host()
        _client = Langfuse(host=host) if host else Langfuse()
    except Exception:
        _client = None  # never break a run because tracing failed
    return _client


class _NoSpan:
    """A handle that quacks like a span but does nothing (tracing disabled)."""

    def update(self, **_: Any) -> None: ...
    def score(self, *_: Any, **__: Any) -> None: ...
    def step(self, *_: Any, **__: Any) -> None: ...
    def __enter__(self) -> "_NoSpan":
        return self
    def __exit__(self, *_: Any) -> bool:
        return False


#: Module-level no-op handle (safe default for callers that don't open a span).
NOOP = _NoSpan()


class _Span:
    """Thin wrapper over a live Langfuse observation; all calls are best-effort."""

    def __init__(self, client: Any, obs: Any) -> None:
        self._c = client
        self._obs = obs

    def update(self, **kw: Any) -> None:
        try:
            self._obs.update(**kw)
        except Exception:
            pass

    def score(self, name: str, value: float, *, comment: str = "") -> None:
        """Attach one metric as a Langfuse score on this span."""
        try:
            self._obs.score(name=name, value=float(value), comment=comment)
        except Exception:
            pass

    def step(
        self,
        kind: str,
        name: str,
        *,
        input: Any = None,
        output: Any = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        metadata: Optional[dict] = None,
    ) -> None:
        """Record one nested, immediately-closed observation (a single loop step)."""
        c = self._c
        if c is None:
            return
        try:  # pragma: no cover - live only
            with c.start_as_current_observation(
                as_type=_AS_TYPE.get(kind, "span"),
                name=name,
                input=input,
                output=output,
                metadata=metadata or {},
            ) as o:
                if tokens_in or tokens_out:
                    try:
                        o.update(usage_details={"input": tokens_in, "output": tokens_out})
                    except Exception:
                        pass
        except Exception:
            pass


@contextmanager
def task_span(
    name: str, *, input: Any = None, metadata: Optional[dict] = None
) -> Iterator[Any]:
    """Open a task-level trace span; yields a handle with ``.step``/``.score``.

    Yields :data:`NOOP` (a do-nothing handle) when tracing is disabled, so call
    sites are identical on and off the traced path.
    """
    c = _get_client()
    if c is None:
        yield NOOP
        return
    try:  # pragma: no cover - live only
        with c.start_as_current_observation(
            as_type="agent", name=name, input=input, metadata=metadata or {}
        ) as obs:
            yield _Span(c, obs)
    except Exception:
        yield NOOP


def flush() -> None:
    """Flush buffered traces (no-op when disabled). Call at run end."""
    c = _get_client()
    if c is not None:  # pragma: no cover - live only
        try:
            c.flush()
        except Exception:
            pass


__all__ = ["enabled", "task_span", "flush", "NOOP"]
