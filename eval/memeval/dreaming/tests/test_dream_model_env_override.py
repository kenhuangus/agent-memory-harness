"""Regression test: DREAM_MODEL + DREAM_PROVIDER env vars flow through make_client.

Belongs to the ADR-dreaming-022 model-swap test. Guards the contract that an
operator can override the subconscious LLM by setting `DREAM_MODEL` (and
optionally `DREAM_PROVIDER`) in `.env` or the shell — verified by both the
shipping plugin's Stop-hook and the bench runner via `load_root_dotenv()`.

If this test ever goes red, the .env wiring is broken and every downstream
prompt-variant A/B is invalid (operator can't trust the model identity they
think they set).
"""

from __future__ import annotations

import pytest

from memeval.dreaming.llm import (
    DEFAULT_MODEL,
    AnthropicClient,
    EchoClient,
    LocalClient,
    OpenRouterClient,
    make_client,
)


def test_default_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DREAM_* env → openrouter provider + DEFAULT_MODEL (ADR-dreaming-004)."""
    monkeypatch.delenv("DREAM_PROVIDER", raising=False)
    monkeypatch.delenv("DREAM_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = make_client()
    assert isinstance(client, OpenRouterClient)
    assert client.model == DEFAULT_MODEL


def test_dream_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """DREAM_MODEL env → OpenRouterClient(model=that_value). Provider unset → openrouter default."""
    monkeypatch.delenv("DREAM_PROVIDER", raising=False)
    monkeypatch.setenv("DREAM_MODEL", "deepseek/deepseek-v4-flash")
    client = make_client()
    assert isinstance(client, OpenRouterClient)
    assert client.model == "deepseek/deepseek-v4-flash"


def test_dream_provider_and_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """DREAM_PROVIDER=local + DREAM_MODEL=ollama:foo → LocalClient(model='ollama:foo')."""
    monkeypatch.setenv("DREAM_PROVIDER", "local")
    monkeypatch.setenv("DREAM_MODEL", "ollama:foo")
    client = make_client()
    assert isinstance(client, LocalClient)
    assert client.model == "ollama:foo"


def test_dream_provider_echo_ignores_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """DREAM_PROVIDER=echo → EchoClient (no model field exposed; deterministic fake)."""
    monkeypatch.setenv("DREAM_PROVIDER", "echo")
    monkeypatch.setenv("DREAM_MODEL", "ignored")
    client = make_client()
    assert isinstance(client, EchoClient)


def test_dream_provider_anthropic_routes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """DREAM_PROVIDER=anthropic + DREAM_MODEL=claude-x → AnthropicClient(model='claude-x')."""
    monkeypatch.setenv("DREAM_PROVIDER", "anthropic")
    monkeypatch.setenv("DREAM_MODEL", "claude-haiku-4-5")
    client = make_client()
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-haiku-4-5"


def test_dream_provider_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown DREAM_PROVIDER raises a clear ValueError naming the legal options."""
    monkeypatch.setenv("DREAM_PROVIDER", "made-up-provider")
    with pytest.raises(ValueError) as exc_info:
        make_client()
    msg = str(exc_info.value)
    assert "made-up-provider" in msg
    assert "openrouter" in msg
    assert "echo" in msg
    assert "local" in msg
    assert "anthropic" in msg


def test_explicit_args_win_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit make_client(provider=, model=) args win over DREAM_* env vars."""
    monkeypatch.setenv("DREAM_PROVIDER", "openrouter")
    monkeypatch.setenv("DREAM_MODEL", "from-env")
    client = make_client(provider="echo", model="from-arg")
    assert isinstance(client, EchoClient)
    # Even when model arg is set, EchoClient doesn't take one; the contract is
    # that explicit args route the provider choice, not just override the model.
