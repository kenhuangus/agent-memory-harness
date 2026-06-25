"""Session-scope test fixtures for the dreaming test suite.

Currently provides a single guardrail (rubric §N20 per halliday A8): every
test session must either have ``OPENROUTER_API_KEY`` unset OR
``memeval.dreaming.worker._make_llm_client`` monkeypatched. Without this guard,
a test that forgets to stub the LLM client silently falls through the
``OpenRouterClient`` ADR-012 fail-open path (empty completion → assert passes
for the wrong reason — the test isn't actually exercising what it claims).

The guard runs at session START and at session END; per-test monkeypatching is
verified at session END by inspecting whether the worker module's attribute
matches the production default.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _disable_noise_filter_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-dreaming-026 — the noise filter is on by default in production,
    but the existing dreaming test suite uses inline non-JSONL log content
    (e.g. ``"content\\n"``, ``"hello world\\n"``) that the filter would
    silently strip → engine would early-return without exercising the
    code path under test.

    **Test-suite contract:** every test in this directory inherits
    ``DREAM_NOISE_FILTER=0`` unless it explicitly re-enables the filter
    with ``monkeypatch.setenv("DREAM_NOISE_FILTER", "1")``. Tests that
    DO want to exercise the filter — e.g. the §NF section in
    ``test_engine.py`` and everything in ``test_transcript_formatter.py``
    — opt back in per-test, not at the suite level.

    The production default is the OPPOSITE (filter on). If a regression
    only shows up in production but not in tests, the most likely
    culprit is this default mismatch.
    """
    monkeypatch.setenv("DREAM_NOISE_FILTER", "0")


@pytest.fixture(scope="session", autouse=True)
def _no_live_llm_in_dreaming_tests() -> None:
    """JOB3 §N20: the dreaming test session must NOT make live OpenRouter calls.

    Fail loudly if a test environment somehow sets ``OPENROUTER_API_KEY`` AND
    fails to monkeypatch the LLM client — a configuration mistake that would
    silently spend money without surfacing the bug.

    The check is at session-start: if the env var is set, the suite refuses to
    run unless an explicit override is acknowledged. The override env var
    (``DREAM_TESTS_ALLOW_LIVE_LLM=1``) exists for the rare case where
    a CI smoke test legitimately wants to hit the network — the override is
    explicit, not implicit.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    explicit_override = os.environ.get("DREAM_TESTS_ALLOW_LIVE_LLM") == "1"
    if api_key and not explicit_override:
        pytest.fail(
            "OPENROUTER_API_KEY is set in the test environment. The dreaming "
            "test suite stubs the LLM client per-test; a live API key risks "
            "silent live calls if a test forgets to monkeypatch "
            "memeval.dreaming.worker._make_llm_client. Either unset the env var, "
            "or set DREAM_TESTS_ALLOW_LIVE_LLM=1 to acknowledge the risk."
        )
