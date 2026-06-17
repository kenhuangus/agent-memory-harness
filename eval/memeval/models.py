"""Model adapters for the AI Agent Memory Harness.

Two concrete :class:`~memeval.protocols.ModelAdapter` implementations plus a
factory:

EchoModel
    Offline, deterministic, network-free. It "answers" a prompt by extracting
    a normalized candidate answer from the prompt itself (a reference/answer
    line, a retrieved-memory line, or the leading sentence). Because the answer
    is derived from whatever context the harness puts in front of it, the
    accuracy metric is actually exercisable offline: feed the gold answer (or a
    memory item that contains it) into the prompt and EchoModel will surface it,
    so memory-on vs memory-off accuracy differs. Token counts use a
    whitespace-word heuristic (``words * 1.3``) so they are stable across runs.

AnthropicAdapter
    Thin wrapper over the official ``anthropic`` SDK. The SDK is imported
    *lazily* inside ``__init__``/``generate`` so importing this module (and the
    whole offline path) never requires the package. Token counts come straight
    from the API response usage block.

Both satisfy the frozen :class:`~memeval.protocols.ModelAdapter` protocol
(``name``, ``price_in``, ``price_out`` attributes plus a ``generate`` returning
``(text, tokens_in, tokens_out)``). Prices are USD per *million* tokens, the
single currency unit used everywhere in the harness.

Standard-library only at import time; ``anthropic`` is the sole optional
dependency and is reached only on the live path.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .cost import price_for
from .protocols import ModelAdapter

# --------------------------------------------------------------------------- #
# Token estimation
# --------------------------------------------------------------------------- #
#: Average characters per token for the contract-specified char heuristic.
_CHARS_PER_TOKEN = 4
#: Word -> token inflation factor for the whitespace heuristic (subword units).
_WORDS_TO_TOKENS = 1.3

# A "reference" / "answer" line in a prompt looks like ``Answer: 42`` or
# ``Reference: foo``. Captured group 1 is the label, group 2 the payload.
_ANSWER_LINE = re.compile(
    r"^\s*(answer|reference|gold|expected|label|result)\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# A retrieved-memory line the harness injects, e.g. ``[memory] Paris is ...``.
_MEMORY_LINE = re.compile(r"^\s*\[(?:memory|mem|recall|context)\][:\-]?\s*(.+?)\s*$",
                          re.IGNORECASE | re.MULTILINE)
# Sentence terminators for "first sentence" fallback.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    """Estimate token count of ``text`` with a stdlib char heuristic.

    Uses ``len(text) // 4`` (≈ 4 chars/token for English-ish text), floored at
    1 so even an empty string costs a token. This is the contract's portable
    estimator used wherever a real tokenizer is unavailable.
    """
    if not text:
        return 1
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_tokens_words(text: str) -> int:
    """Estimate tokens from whitespace word count (``words * 1.3``).

    Closer to subword tokenization for prose; used by :class:`EchoModel` so its
    reported counts track word count rather than raw characters. Floored at 1.
    """
    if not text or not text.strip():
        return 1
    words = len(text.split())
    return max(1, int(round(words * _WORDS_TO_TOKENS)))


# --------------------------------------------------------------------------- #
# Answer extraction (shared by EchoModel)
# --------------------------------------------------------------------------- #
def _extract_answer(prompt: str, system: Optional[str]) -> str:
    """Derive a deterministic candidate answer from prompt/system text.

    Priority (most-specific first):
      1. An explicit ``Answer:``/``Reference:``/``Gold:`` line (last one wins,
         so a later, more-specific reference overrides an earlier one).
      2. A harness-injected ``[memory] ...`` retrieved line (last one wins).
      3. The first non-empty, non-instruction sentence of the prompt.
      4. The first non-empty line of the system prompt.
      5. Empty string.

    Surfacing (1)/(2) is what makes EchoModel's accuracy *memory-sensitive*:
    when the harness puts the gold answer (or a retrieved memory carrying it)
    into the prompt, EchoModel echoes it and scores correct; without it, the
    model falls back to the question's leading sentence and typically misses.
    """
    text = prompt or ""

    answers = _ANSWER_LINE.findall(text)
    if answers:
        # findall returns (label, payload) tuples; take the last payload.
        return answers[-1][1].strip()

    mems = _MEMORY_LINE.findall(text)
    if mems:
        return mems[-1].strip()

    # Fallback: first meaningful sentence of the prompt body.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip obvious instruction/scaffolding lines.
        low = stripped.lower()
        if low.startswith(("question", "system", "instruction", "user:",
                           "context", "you are", "###", "---")):
            continue
        sentences = _SENTENCE_SPLIT.split(stripped)
        if sentences and sentences[0].strip():
            return sentences[0].strip()

    if system and system.strip():
        for line in system.splitlines():
            if line.strip():
                return line.strip()

    return ""


def _apply_stop(text: str, stop: Optional[list[str]]) -> str:
    """Truncate ``text`` at the earliest stop sequence (mirrors API ``stop``)."""
    if not stop:
        return text
    cut = len(text)
    for s in stop:
        if not s:
            continue
        idx = text.find(s)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut]


# --------------------------------------------------------------------------- #
# EchoModel -- offline deterministic adapter
# --------------------------------------------------------------------------- #
class EchoModel:
    """Deterministic offline model: echoes an answer extracted from context.

    Satisfies :class:`~memeval.protocols.ModelAdapter`. Free (prices 0). Given
    the same ``(prompt, system, stop)`` it always returns the same text, so
    smoke tests and metric math are reproducible. ``temperature`` is accepted
    and ignored (output is always the deterministic extraction).

    Parameters
    ----------
    name:
        Adapter name reported on trajectories/configs (default ``"echo"``).
    reply:
        If given, EchoModel returns this fixed string for every call instead of
        extracting from the prompt (useful for forcing a known prediction in
        tests).
    """

    name: str = "echo"
    price_in: float = 0.0
    price_out: float = 0.0

    def __init__(self, *, name: str = "echo", reply: Optional[str] = None) -> None:
        self.name = name
        self.reply = reply
        self.price_in = 0.0
        self.price_out = 0.0

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
        """Return ``(text, tokens_in, tokens_out)`` deterministically.

        ``text`` is the fixed ``reply`` if configured, else the answer extracted
        from ``prompt``/``system`` via :func:`_extract_answer`. ``tokens_in``
        counts prompt+system words (``* 1.3``); ``tokens_out`` counts the reply
        words. ``stop`` truncates the reply; ``max_tokens`` caps reply tokens.
        ``temperature`` is ignored (deterministic).
        """
        if self.reply is not None:
            text = self.reply
        else:
            text = _extract_answer(prompt, system)

        text = _apply_stop(text, stop)

        # Cap output length to max_tokens (word-budget approximation).
        if max_tokens and max_tokens > 0:
            words = text.split()
            budget = max(1, int(max_tokens / _WORDS_TO_TOKENS))
            if len(words) > budget:
                text = " ".join(words[:budget])

        prompt_text = prompt or ""
        if system:
            prompt_text = f"{system}\n{prompt_text}"
        tokens_in = estimate_tokens_words(prompt_text)
        tokens_out = estimate_tokens_words(text)
        return text, tokens_in, tokens_out


# --------------------------------------------------------------------------- #
# AnthropicAdapter -- live adapter (lazy anthropic import)
# --------------------------------------------------------------------------- #
class AnthropicAdapter:
    """Live Claude adapter wrapping the official ``anthropic`` SDK.

    The SDK is imported lazily (inside ``__init__`` and ``generate``) so this
    module imports with the standard library alone; ``anthropic`` is only
    required when an instance is actually constructed/used. Prices default to
    the :data:`memeval.cost.PRICING` entry for ``model`` (USD per million
    tokens) but may be overridden.

    Parameters
    ----------
    model:
        Anthropic model id, e.g. ``"claude-haiku-4-5"``.
    api_key:
        Explicit key; if ``None`` the SDK reads ``api_key_env`` from the
        environment (default ``ANTHROPIC_API_KEY``).
    api_key_env:
        Environment variable name holding the key when ``api_key`` is ``None``.
    price_in / price_out:
        Override the looked-up prices (USD per million tokens).
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        price_in: Optional[float] = None,
        price_out: Optional[float] = None,
    ) -> None:
        import os

        try:
            import anthropic  # noqa: F401  (lazy: presence check only)
        except ImportError as exc:  # pragma: no cover - exercised only live
            raise ImportError(
                "AnthropicAdapter requires the 'anthropic' package. "
                "Install it with `pip install anthropic` (it is an optional "
                "dependency; the offline EchoModel path needs no extra deps)."
            ) from exc

        self.name = model
        self.model = model
        self.api_key_env = api_key_env
        self._api_key = api_key if api_key is not None else os.environ.get(api_key_env)

        prices = price_for(model)
        self.price_in = price_in if price_in is not None else prices["in"]
        self.price_out = price_out if price_out is not None else prices["out"]

        self._client: Any = None  # constructed on first generate()

    def _ensure_client(self) -> Any:
        """Construct (once) and return the anthropic client. Lazy import."""
        if self._client is None:
            import anthropic

            if self._api_key:
                self._client = anthropic.Anthropic(api_key=self._api_key)
            else:
                # Let the SDK pull the key from its default env var.
                self._client = anthropic.Anthropic()
        return self._client

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
        """Call the Messages API and return ``(text, tokens_in, tokens_out)``.

        Token counts are the real billed ``usage.input_tokens`` /
        ``usage.output_tokens`` from the response. ``stop`` maps to
        ``stop_sequences``; ``system``/``temperature``/``max_tokens`` map to
        their SDK equivalents. Extra ``kwargs`` are forwarded to the API call.
        """
        client = self._ensure_client()

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            params["system"] = system
        if stop:
            params["stop_sequences"] = stop
        params.update(kwargs)

        resp = client.messages.create(**params)

        # Concatenate all text blocks of the response content.
        parts: list[str] = []
        for block in getattr(resp, "content", []) or []:
            btext = getattr(block, "text", None)
            if btext is not None:
                parts.append(btext)
        text = "".join(parts)

        usage = getattr(resp, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        # Fallback to estimates if the API omits usage (defensive).
        if tokens_in == 0:
            tokens_in = estimate_tokens((system or "") + prompt)
        if tokens_out == 0:
            tokens_out = estimate_tokens(text)
        return text, tokens_in, tokens_out


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
#: Spec strings that resolve to the offline EchoModel.
_ECHO_SPECS = {"echo", "echomodel", "offline", "test", "mock"}


def get_model(spec: str | ModelAdapter | None, **kwargs: Any) -> ModelAdapter:
    """Resolve a model spec into a concrete :class:`ModelAdapter`.

    Accepts:
      * ``None`` / ``"echo"`` (and aliases) -> :class:`EchoModel` (offline).
      * an already-constructed :class:`ModelAdapter` -> returned unchanged
        (so callers can pass an instance through transparently).
      * any other string -> treated as an Anthropic model id and wrapped in
        :class:`AnthropicAdapter` (lazy-imports ``anthropic`` at construction).

    Extra ``kwargs`` are forwarded to the chosen adapter's constructor
    (e.g. ``api_key_env``, ``price_in``, ``reply``). Picking EchoModel keeps the
    offline path dependency-free; only a real model id pulls in ``anthropic``.
    """
    if spec is None:
        return EchoModel(**kwargs)

    # Already an adapter (duck-typed): pass through.
    if hasattr(spec, "generate") and not isinstance(spec, str):
        return spec  # type: ignore[return-value]

    if isinstance(spec, str):
        key = spec.strip().lower()
        if key in _ECHO_SPECS:
            return EchoModel(**kwargs)
        return AnthropicAdapter(spec, **kwargs)

    raise TypeError(f"Cannot resolve model spec of type {type(spec)!r}: {spec!r}")


__all__ = [
    "estimate_tokens",
    "estimate_tokens_words",
    "EchoModel",
    "AnthropicAdapter",
    "get_model",
]
