"""Daydream LLMClient — ADRs 006 + 010 + 012 + 004.

The subconscious's text-generation seam. Both Daydream (PR4) and
Dreaming (later) share this interface; v1 ships an OpenRouter-backed
default and an ``EchoClient`` stub for tests. ``LocalClient`` (Ollama)
and ``AnthropicClient`` are named in ADR-dreaming-006's roster; their
bodies are ``NotImplementedError`` stubs until a concrete consumer
drives the spec.

Lazy imports: ``httpx`` and any provider SDK load inside ``complete()``
keeping the offline path stdlib-only at module top per
``architecture.md`` §3.

Trust boundary: ``LLMClient.complete()`` accepts only ``RedactedText``
per ADR-dreaming-010 — mypy ``--strict`` enforces. The producer of
``RedactedText`` is ``memeval.dreaming.redaction.redact()``; the LLM
client never sees raw ``str``.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .events import emit
from .redaction import RedactedText

_logger = logging.getLogger(__name__)

#: OpenRouter chat-completions endpoint (verified 2026-06-21 at
#: https://openrouter.ai/docs/quickstart).
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Default Daydream model per ADR-dreaming-004.
DEFAULT_MODEL = "inclusionai/ling-2.6-flash"

#: Default max-tokens for a single completion. Conservative; callers can
#: override per call. Daydream extraction prompts are bounded; this cap
#: is "model wouldn't reasonably want to exceed" rather than "we'd panic
#: if it did."
DEFAULT_MAX_TOKENS = 4096

__all__ = [
    "Completion",
    "LLMClient",
    "EchoClient",
    "OpenRouterClient",
    "LocalClient",
    "AnthropicClient",
    "make_client",
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "OPENROUTER_URL",
]


# --------------------------------------------------------------------------- #
# Completion + Protocol (ADR-dreaming-006)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Completion:
    """Result of a single ``LLMClient.complete()`` call.

    ``text`` is the generated text. Empty string indicates the provider
    was unavailable (per ADR-dreaming-012); the caller MUST check this
    and skip downstream processing without advancing any cursor state
    (per ADR-dreaming-013).

    ``tokens_in`` / ``tokens_out`` are the provider-reported token
    counts. Zero when the call didn't actually happen (provider
    unavailable, missing ``usage`` field, etc.) per ADR-dreaming-006
    + ADR-dreaming-012.
    """

    text: str
    tokens_in: int
    tokens_out: int


class LLMClient(Protocol):
    """Swappable subconscious model client (ADR-dreaming-006).

    Per ADR-dreaming-010, ``complete()`` accepts only ``RedactedText`` —
    never raw ``str``. mypy ``--strict`` enforces the trust boundary at
    type-check time.

    Implementations: ``OpenRouterClient`` (default), ``EchoClient`` (tests),
    ``LocalClient`` (Ollama, stub), ``AnthropicClient`` (stub).
    """

    model: str

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        """Generate one completion. Returns ``Completion('', 0, 0)`` when the
        provider is unavailable (fail-open per ADR-dreaming-012)."""
        ...


# --------------------------------------------------------------------------- #
# EchoClient (deterministic, no network)
# --------------------------------------------------------------------------- #
class EchoClient:
    """Deterministic no-network ``LLMClient`` for tests + offline path.

    Echoes the input as the completion text (truncated to ``max_tokens *
    4`` chars), computes synthetic token counts via the OpenAI char/4
    heuristic. Model name is ``"echo"`` so :mod:`memeval.cost` reports
    $0 spend (matches the ``echo`` ``PRICING`` entry).
    """

    model = "echo"

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        prompt_chars = len(prompt) + (len(system) if system else 0)
        cap = max(0, max_tokens) * 4
        out_text = str(prompt)[:cap] if cap > 0 else ""
        return Completion(
            text=out_text,
            tokens_in=prompt_chars // 4,
            tokens_out=len(out_text) // 4,
        )


# --------------------------------------------------------------------------- #
# OpenRouterClient (default — real HTTP via httpx, lazy-imported)
# --------------------------------------------------------------------------- #
class OpenRouterClient:
    """Default ``LLMClient`` — OpenRouter via ``httpx``.

    ADR-dreaming-012: when ``OPENROUTER_API_KEY`` is unset, ``complete()``
    does NOT call the network; returns ``Completion('', 0, 0)`` and emits
    an ``llm_unavailable`` event. The Daydream chunk loop (PR4) checks
    empty text and skips memory extraction without advancing the cursor.

    Network failure modes (timeouts, 401/403/429/5xx, malformed JSON)
    all degrade to the same shape: empty ``Completion`` + an event with
    the failure subtype. Caller's behavior is identical to the
    unavailable case (no cursor advance per ADR-dreaming-013).

    Transient errors (HTTP 429 + 5xx) are retried with bounded
    exponential backoff (``max_retries`` extra attempts). A ``Retry-After``
    response header, when present and a valid integer, sets the delay;
    otherwise ``min(max_backoff_s, backoff_base_s * 2**attempt)`` plus
    jitter is used. Network exceptions and non-429 4xx are NOT retried.
    Once retries are exhausted the FAIL-OPEN contract is unchanged: the
    same empty ``Completion`` + the same terminal event as before
    (``llm_rate_limited`` for 429, ``llm_call_failed`` for 5xx), so the
    engine's cursor semantics (ADR-dreaming-012/013) are preserved.

    ``httpx`` is lazy-imported inside ``complete()`` per architecture.md
    §3 (offline path stays stdlib-only at module top).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        url: str = OPENROUTER_URL,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.5,
        max_backoff_s: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        # Honor an explicit api_key=None passthrough vs the env-var lookup:
        # caller may have decided "I want this client without a key" for
        # testing; only fall back to env when api_key was not explicitly
        # passed.
        self._api_key = (
            api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        )
        self._url = url
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        # ADR-dreaming-012: missing-key fail-open — no network, no exception.
        if not self._api_key:
            emit(
                "llm_unavailable",
                provider="openrouter",
                reason="OPENROUTER_API_KEY unset",
                model=self.model,
            )
            return Completion(text="", tokens_in=0, tokens_out=0)

        # Lazy-import per architecture.md §3.
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "OpenRouterClient requires httpx. Install with: "
                "pip install 'agent-memory-eval[daydream]'"
            ) from exc

        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": str(prompt)})

        # Bounded retry on transient errors (429 + 5xx). One LLM call per
        # daydream hook means a single transient 429 would otherwise lose
        # the whole hook's memory extraction; retrying recovers it.
        response = None
        for attempt in range(self._max_retries + 1):
            try:
                response = httpx.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                    },
                    timeout=self._timeout_s,
                )
            except Exception as exc:
                # Network exceptions are NOT retried (tight scope): emit +
                # return empty, exactly as before.
                emit(
                    "llm_call_failed",
                    provider="openrouter",
                    reason=f"{type(exc).__name__}: {exc}",
                    model=self.model,
                )
                return Completion(text="", tokens_in=0, tokens_out=0)

            status = response.status_code
            transient = status == 429 or status >= 500
            if transient and attempt < self._max_retries:
                delay = self._retry_delay(response, attempt)
                emit(
                    "llm_retry",
                    provider="openrouter",
                    status=status,
                    attempt=attempt,
                    delay_s=delay,
                    model=self.model,
                )
                self._sleep(delay)
                continue
            break

        assert response is not None  # loop always runs >= 1 iteration
        status = response.status_code
        if status == 429:
            # Retries exhausted (or none configured): unchanged fail-open.
            emit(
                "llm_rate_limited",
                provider="openrouter",
                status=status,
                model=self.model,
            )
            return Completion(text="", tokens_in=0, tokens_out=0)
        if status >= 400:
            emit(
                "llm_call_failed",
                provider="openrouter",
                status=status,
                model=self.model,
            )
            return Completion(text="", tokens_in=0, tokens_out=0)

        try:
            data: dict[str, Any] = response.json()
        except Exception as exc:
            emit(
                "llm_malformed_response",
                provider="openrouter",
                reason=f"{type(exc).__name__}: {exc}",
                model=self.model,
            )
            return Completion(text="", tokens_in=0, tokens_out=0)

        try:
            choices = data["choices"]
            text = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            emit(
                "llm_malformed_response",
                provider="openrouter",
                reason="missing choices[0].message.content",
                model=self.model,
            )
            return Completion(text="", tokens_in=0, tokens_out=0)

        usage = data.get("usage") or {}
        try:
            tokens_in = int(usage.get("prompt_tokens", 0))
            tokens_out = int(usage.get("completion_tokens", 0))
        except (TypeError, ValueError):
            # Usage field present but malformed -- log + zero counts.
            emit(
                "llm_malformed_response",
                provider="openrouter",
                reason="non-integer usage tokens",
                model=self.model,
            )
            tokens_in = 0
            tokens_out = 0

        # Symmetry with failure-path emits (llm_unavailable / llm_call_failed /
        # llm_retry / llm_rate_limited / llm_malformed_response above): record
        # which model actually answered, so events.jsonl carries the model name
        # on healthy runs too. Closes the observability asymmetry per ADR-dreaming-022.
        emit(
            "llm_call_succeeded",
            provider="openrouter",
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        return Completion(text=str(text or ""), tokens_in=tokens_in, tokens_out=tokens_out)

    def _retry_delay(self, response: Any, attempt: int) -> float:
        """Compute the pre-retry sleep for a transient error.

        Honors a valid integer ``Retry-After`` header (checked case-
        insensitively, read defensively); otherwise falls back to
        ``min(max_backoff_s, backoff_base_s * 2**attempt)`` plus jitter.
        """
        hdrs = getattr(response, "headers", {}) or {}
        raw = hdrs.get("Retry-After")
        if raw is None:
            raw = hdrs.get("retry-after")
        if raw is not None:
            try:
                return float(int(str(raw).strip()))
            except (TypeError, ValueError):
                pass
        backoff = min(self._max_backoff_s, self._backoff_base_s * (2 ** attempt))
        return backoff + random.uniform(0, self._backoff_base_s)


# --------------------------------------------------------------------------- #
# LocalClient + AnthropicClient (named-stub roster per ADR-dreaming-006)
# --------------------------------------------------------------------------- #
class LocalClient:
    """``LLMClient`` backed by a local model (e.g. Ollama).

    Named in ADR-dreaming-006 §Roster as the privacy-sensitive alternate
    impl. NOT YET IMPLEMENTED — a concrete consumer's needs (Ollama vs
    llama.cpp, streaming, etc.) drive the spec; see ADR-006 §Open items.
    Set ``DREAM_PROVIDER=openrouter`` or ``=echo`` until a real impl ships.
    """

    def __init__(self, model: str = "ollama:llama3") -> None:
        self.model = model

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        raise NotImplementedError(
            "LocalClient is named in ADR-dreaming-006 §Roster but not yet "
            "implemented. Set DREAM_PROVIDER=openrouter or =echo, or "
            "implement LocalClient in a follow-up PR."
        )


class AnthropicClient:
    """``LLMClient`` backed by Anthropic's native API.

    Named in ADR-dreaming-006 §Roster. NOT YET IMPLEMENTED — the
    contract would require an additional anthropic SDK dep + env-var
    handling; deferred until a concrete consumer drives the spec.
    """

    def __init__(self, model: str = "claude-haiku-4-5") -> None:
        self.model = model

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        raise NotImplementedError(
            "AnthropicClient is named in ADR-dreaming-006 §Roster but not "
            "yet implemented. Set DREAM_PROVIDER=openrouter or =echo, or "
            "implement AnthropicClient in a follow-up PR."
        )


# --------------------------------------------------------------------------- #
# Factory (DREAM_PROVIDER + DREAM_MODEL env-var dispatch)
# --------------------------------------------------------------------------- #
def make_client(
    *,
    provider: str | None = None,
    model: str | None = None,
) -> LLMClient:
    """Construct an ``LLMClient`` by env-var dispatch (ADR-dreaming-004).

    Defaults: ``DREAM_PROVIDER=openrouter``, ``DREAM_MODEL=<DEFAULT_MODEL>``.
    Explicit args override env vars.
    """
    p = (provider if provider is not None else os.environ.get("DREAM_PROVIDER", "openrouter")).strip()
    m = model if model is not None else os.environ.get("DREAM_MODEL", DEFAULT_MODEL)
    if p == "openrouter":
        return OpenRouterClient(model=m)
    if p == "echo":
        return EchoClient()
    if p == "local":
        return LocalClient(model=m)
    if p == "anthropic":
        return AnthropicClient(model=m)
    raise ValueError(
        f"Unknown DREAM_PROVIDER={p!r}; expected one of: "
        "openrouter, echo, local, anthropic"
    )
