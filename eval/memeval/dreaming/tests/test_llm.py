"""LLMClient tests — ADR-dreaming-006 / 010 / 012 / 004.

Covers:
  - `Completion` dataclass shape (frozen, slots, fields)
  - `EchoClient` determinism + synthetic token counts
  - `OpenRouterClient` missing-key behavior (ADR-012 — no network, empty
    completion, `llm_unavailable` event)
  - `OpenRouterClient` happy path (mocked httpx)
  - `OpenRouterClient` failure modes (401/403/429/5xx, network error,
    malformed response) — all fail-open per ADR-005/006/012
  - `LocalClient` / `AnthropicClient` stubs raise clear NotImplementedError
  - `make_client()` factory dispatch on DREAM_PROVIDER / DREAM_MODEL
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

pytest.importorskip(
    "detect_secrets",
    reason="install with `pip install -e eval[daydream]` to run dreaming tests",
)

from memeval.dreaming.events import emit
from memeval.dreaming.llm import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    OPENROUTER_URL,
    AnthropicClient,
    Completion,
    EchoClient,
    LocalClient,
    OpenRouterClient,
    make_client,
)
from memeval.dreaming.redaction import RedactedText


# --- Completion dataclass shape ------------------------------------------- #
def test_completion_is_frozen():
    c = Completion(text="x", tokens_in=1, tokens_out=2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.text = "y"  # type: ignore[misc]  # REASON: test asserts immutability


def test_completion_uses_slots():
    c = Completion(text="x", tokens_in=1, tokens_out=2)
    assert not hasattr(c, "__dict__"), "Completion should use __slots__"


def test_completion_fields_are_typed():
    fields = {f.name: f.type for f in dataclasses.fields(Completion)}
    assert set(fields) == {"text", "tokens_in", "tokens_out"}


# --- EchoClient ----------------------------------------------------------- #
def test_echo_client_model_is_echo():
    """Matches cost.py PRICING entry 'echo' = $0 so test runs don't lie."""
    assert EchoClient().model == "echo"


def test_echo_client_returns_completion_with_text():
    client = EchoClient()
    out = client.complete(RedactedText("hello world"))
    assert isinstance(out, Completion)
    assert out.text == "hello world"


def test_echo_client_synthetic_tokens_in_char_div_4():
    """Per ADR-006 §Open items: EchoClient uses OpenAI char/4 heuristic."""
    client = EchoClient()
    out = client.complete(RedactedText("abcdefgh"))  # 8 chars
    assert out.tokens_in == 8 // 4  # 2


def test_echo_client_with_system_adds_to_tokens_in():
    client = EchoClient()
    out = client.complete(
        RedactedText("user-msg"),  # 8 chars
        system=RedactedText("sys-msg"),  # 7 chars
    )
    assert out.tokens_in == (8 + 7) // 4


def test_echo_client_respects_max_tokens():
    client = EchoClient()
    out = client.complete(RedactedText("x" * 1000), max_tokens=5)
    # Truncated to 5*4 = 20 chars.
    assert len(out.text) == 20
    assert out.tokens_out == 20 // 4


def test_echo_client_is_deterministic():
    client = EchoClient()
    a = client.complete(RedactedText("same input"))
    b = client.complete(RedactedText("same input"))
    assert a == b


# --- OpenRouterClient missing-key fail-open (ADR-012) -------------------- #
def test_openrouter_init_succeeds_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # No raise.
    client = OpenRouterClient(model="x")
    assert client.model == "x"


def test_openrouter_complete_without_key_returns_empty_completion(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = OpenRouterClient()
    out = client.complete(RedactedText("any text"))
    assert out == Completion(text="", tokens_in=0, tokens_out=0)


def test_openrouter_complete_without_key_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # If httpx.post were called, this would import httpx and call it; we
    # poison the symbol so any call fails loudly.
    import httpx

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("httpx.post called despite missing API key")

    monkeypatch.setattr(httpx, "post", boom)
    client = OpenRouterClient()
    client.complete(RedactedText("any text"))  # must not raise


def test_openrouter_complete_without_key_emits_llm_unavailable_event(
    monkeypatch, caplog
):
    import logging

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    client = OpenRouterClient(model="some-model")
    client.complete(RedactedText("x"))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("llm_unavailable" in m for m in msgs), msgs


# --- OpenRouterClient happy path (mocked httpx) -------------------------- #
class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None,
                 *, raise_on_json: bool = False,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self._raise_on_json = raise_on_json
        self.headers = headers if headers is not None else {}

    def json(self) -> Any:
        if self._raise_on_json:
            raise ValueError("malformed JSON")
        return self._body


def _mock_httpx_post(monkeypatch, response: _FakeResponse) -> list[dict[str, Any]]:
    """Replace httpx.post with a recorder that returns the canned response."""
    import httpx

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any],
                  timeout: float) -> _FakeResponse:
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return response

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _mock_httpx_post_seq(
    monkeypatch, responses: list[_FakeResponse]
) -> list[dict[str, Any]]:
    """Replace httpx.post with a recorder returning ``responses`` in order.

    The last response is reused if more calls than responses occur (so a
    test that under-counts still fails on an assertion, not an IndexError).
    """
    import httpx

    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any],
                  timeout: float) -> _FakeResponse:
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        idx = min(len(calls) - 1, len(responses) - 1)
        return responses[idx]

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_openrouter_happy_path_returns_text_and_token_counts(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    response = _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "hello from the model"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        },
    )
    calls = _mock_httpx_post(monkeypatch, response)

    client = OpenRouterClient(model="test-model")
    out = client.complete(RedactedText("user prompt"))

    assert out == Completion(text="hello from the model", tokens_in=12, tokens_out=5)
    assert len(calls) == 1
    assert calls[0]["url"] == OPENROUTER_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0]["json"]["model"] == "test-model"
    assert calls[0]["json"]["messages"] == [{"role": "user", "content": "user prompt"}]


def test_openrouter_includes_system_message_when_provided(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    response = _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    calls = _mock_httpx_post(monkeypatch, response)

    client = OpenRouterClient()
    client.complete(RedactedText("user"), system=RedactedText("system instructions"))

    assert calls[0]["json"]["messages"] == [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "user"},
    ]


def test_openrouter_passes_max_tokens(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    response = _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    calls = _mock_httpx_post(monkeypatch, response)

    OpenRouterClient().complete(RedactedText("hi"), max_tokens=1234)
    assert calls[0]["json"]["max_tokens"] == 1234


# --- OpenRouterClient error paths --------------------------------------- #
@pytest.mark.parametrize("status", [400, 401, 403, 500, 502, 503])
def test_openrouter_status_error_returns_empty_completion(monkeypatch, status):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = _mock_httpx_post(monkeypatch, _FakeResponse(status, {}))
    client = OpenRouterClient(sleep=lambda _: None)
    out = client.complete(RedactedText("x"))
    assert out == Completion(text="", tokens_in=0, tokens_out=0)
    if status >= 500:
        # 5xx is transient → retried max_retries+1 times.
        assert len(calls) == client._max_retries + 1
    else:
        # Non-429 4xx is NOT retried.
        assert len(calls) == 1


def test_openrouter_429_emits_rate_limited_event(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = _mock_httpx_post(monkeypatch, _FakeResponse(429, {}))
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    client = OpenRouterClient(sleep=lambda _: None)
    out = client.complete(RedactedText("x"))
    assert out.text == ""
    assert any("llm_rate_limited" in r.getMessage() for r in caplog.records)
    assert len(calls) == client._max_retries + 1


def _ok_response() -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "recovered text"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
    )


def test_openrouter_429_then_200_recovers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = _mock_httpx_post_seq(
        monkeypatch, [_FakeResponse(429, {}), _ok_response()]
    )
    client = OpenRouterClient(sleep=lambda _: None)
    out = client.complete(RedactedText("x"))
    assert out == Completion(text="recovered text", tokens_in=3, tokens_out=4)
    assert len(calls) == 2


def test_openrouter_5xx_then_200_recovers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = _mock_httpx_post_seq(
        monkeypatch, [_FakeResponse(503, {}), _ok_response()]
    )
    client = OpenRouterClient(sleep=lambda _: None)
    out = client.complete(RedactedText("x"))
    assert out == Completion(text="recovered text", tokens_in=3, tokens_out=4)
    assert len(calls) == 2


def test_openrouter_retry_after_header_honored(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    slept: list[float] = []
    _mock_httpx_post_seq(
        monkeypatch,
        [_FakeResponse(429, {}, headers={"Retry-After": "2"}), _ok_response()],
    )
    client = OpenRouterClient(sleep=slept.append)
    client.complete(RedactedText("x"))
    assert slept == [2.0]


def test_openrouter_retry_after_header_case_insensitive(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    slept: list[float] = []
    _mock_httpx_post_seq(
        monkeypatch,
        [_FakeResponse(429, {}, headers={"retry-after": "3"}), _ok_response()],
    )
    client = OpenRouterClient(sleep=slept.append)
    client.complete(RedactedText("x"))
    assert slept == [3.0]


def test_openrouter_exhausted_retries_empty_and_rate_limited(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = _mock_httpx_post(monkeypatch, _FakeResponse(429, {}))
    caplog.set_level(logging.DEBUG, logger="memeval.dreaming.events")
    client = OpenRouterClient(sleep=lambda _: None)
    out = client.complete(RedactedText("x"))
    assert out == Completion(text="", tokens_in=0, tokens_out=0)
    assert any("llm_rate_limited" in r.getMessage() for r in caplog.records)
    assert len(calls) == client._max_retries + 1


def test_openrouter_default_backoff_sequence_no_retry_after(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # Deterministic jitter: pin random.uniform to 0.
    import memeval.dreaming.llm as llm_mod

    monkeypatch.setattr(llm_mod.random, "uniform", lambda a, b: 0.0)
    slept: list[float] = []
    _mock_httpx_post(monkeypatch, _FakeResponse(429, {}))
    client = OpenRouterClient(
        sleep=slept.append,
        max_retries=3,
        backoff_base_s=0.5,
        max_backoff_s=8.0,
    )
    client.complete(RedactedText("x"))
    # attempts 0,1,2 (3rd attempt is the last, no sleep): 0.5, 1.0, 2.0
    assert slept == [0.5, 1.0, 2.0]


def test_openrouter_network_error_returns_empty_completion(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    import httpx

    def raises(*a: Any, **k: Any) -> Any:
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(httpx, "post", raises)
    out = OpenRouterClient().complete(RedactedText("x"))
    assert out == Completion(text="", tokens_in=0, tokens_out=0)


def test_openrouter_malformed_response_returns_empty_completion(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # JSON parse fails.
    _mock_httpx_post(
        monkeypatch, _FakeResponse(200, {}, raise_on_json=True),
    )
    out = OpenRouterClient().complete(RedactedText("x"))
    assert out == Completion(text="", tokens_in=0, tokens_out=0)


def test_openrouter_missing_choices_returns_empty_completion(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _mock_httpx_post(monkeypatch, _FakeResponse(200, {"usage": {}}))
    out = OpenRouterClient().complete(RedactedText("x"))
    assert out == Completion(text="", tokens_in=0, tokens_out=0)


def test_openrouter_missing_usage_returns_zero_token_counts(monkeypatch):
    """Per ADR-006 §Consequences: missing usage → log + return zero counts."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _mock_httpx_post(
        monkeypatch,
        _FakeResponse(200, {"choices": [{"message": {"content": "hi"}}]}),
    )
    out = OpenRouterClient().complete(RedactedText("x"))
    assert out == Completion(text="hi", tokens_in=0, tokens_out=0)


def test_openrouter_partial_usage_handles_each_key_independently(monkeypatch):
    """One of prompt_tokens/completion_tokens missing → zero for that side."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _mock_httpx_post(
        monkeypatch,
        _FakeResponse(
            200,
            {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 7},  # no completion_tokens
            },
        ),
    )
    out = OpenRouterClient().complete(RedactedText("x"))
    assert out == Completion(text="hi", tokens_in=7, tokens_out=0)


# --- LocalClient + AnthropicClient stubs -------------------------------- #
def test_local_client_raises_not_implemented_with_clear_message():
    with pytest.raises(NotImplementedError) as exc_info:
        LocalClient().complete(RedactedText("x"))
    msg = str(exc_info.value)
    assert "LocalClient" in msg
    assert "ADR-dreaming-006" in msg


def test_anthropic_client_raises_not_implemented_with_clear_message():
    with pytest.raises(NotImplementedError) as exc_info:
        AnthropicClient().complete(RedactedText("x"))
    msg = str(exc_info.value)
    assert "AnthropicClient" in msg
    assert "ADR-dreaming-006" in msg


def test_local_client_has_model_attribute():
    """Even as a stub, model attr must be set for cost.py compatibility."""
    assert LocalClient().model.startswith("ollama:")


def test_anthropic_client_has_model_attribute():
    assert AnthropicClient().model == "claude-haiku-4-5"


# --- make_client() factory --------------------------------------------- #
def test_make_client_default_is_openrouter_with_default_model(monkeypatch):
    monkeypatch.delenv("DREAM_PROVIDER", raising=False)
    monkeypatch.delenv("DREAM_MODEL", raising=False)
    c = make_client()
    assert isinstance(c, OpenRouterClient)
    assert c.model == DEFAULT_MODEL


def test_make_client_dream_provider_echo(monkeypatch):
    monkeypatch.setenv("DREAM_PROVIDER", "echo")
    assert isinstance(make_client(), EchoClient)


def test_make_client_dream_provider_local(monkeypatch):
    monkeypatch.setenv("DREAM_PROVIDER", "local")
    assert isinstance(make_client(), LocalClient)


def test_make_client_dream_provider_anthropic(monkeypatch):
    monkeypatch.setenv("DREAM_PROVIDER", "anthropic")
    assert isinstance(make_client(), AnthropicClient)


def test_make_client_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("DREAM_PROVIDER", "openrouter")
    monkeypatch.setenv("DREAM_MODEL", "env-model")
    c = make_client(provider="openrouter", model="explicit-model")
    assert c.model == "explicit-model"


def test_make_client_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("DREAM_PROVIDER", "nonexistent-provider")
    with pytest.raises(ValueError) as exc_info:
        make_client()
    assert "nonexistent-provider" in str(exc_info.value)


def test_make_client_dream_model_env_var(monkeypatch):
    monkeypatch.setenv("DREAM_PROVIDER", "openrouter")
    monkeypatch.setenv("DREAM_MODEL", "some-other-model")
    c = make_client()
    assert c.model == "some-other-model"


# --- Constants sanity --------------------------------------------------- #
def test_default_model_matches_adr_004():
    assert DEFAULT_MODEL == "inclusionai/ling-2.6-flash"


def test_default_max_tokens_is_positive():
    assert DEFAULT_MAX_TOKENS > 0


def test_openrouter_url_is_v1_chat_completions():
    """Verified 2026-06-21 at https://openrouter.ai/docs/quickstart."""
    assert OPENROUTER_URL == "https://openrouter.ai/api/v1/chat/completions"
