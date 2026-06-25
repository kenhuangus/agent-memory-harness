"""Pure-function tests for ``memeval.dreaming.transcript_formatter``.

These tests pin the parser's library form (``format_chunk``) — the
CLI form (``main``) is verified incidentally because both call the
same ``_format_lines`` core. The parser ships from the user with a
fixed dispatch over CC-native event types (queue-operation, attachment,
last-prompt, system, plus model turns); these tests pin each branch.

ADR-dreaming-026 documents the contract change this module enables.
"""

from __future__ import annotations

import json

import pytest

from memeval.dreaming.transcript_formatter import format_chunk


def _ev(**fields) -> str:
    return json.dumps(fields)


# --------------------------------------------------------------------------- #
# §A — Empty + degenerate inputs
# --------------------------------------------------------------------------- #
def test_format_chunk_empty_string_returns_empty() -> None:
    assert format_chunk("") == ""


def test_format_chunk_whitespace_only_returns_empty() -> None:
    assert format_chunk("   \n  \n\t\n") == ""


def test_format_chunk_single_malformed_line_returns_empty_no_raise() -> None:
    """Per the engine's bad-byte tolerance — malformed JSONL is silently
    skipped (the same line was previously sent to the LLM as raw bytes;
    now it's dropped at the filter)."""
    assert format_chunk("not json at all\n") == ""
    # ALSO doesn't raise on the truncated-trailing-line case
    assert format_chunk('{"type":"user","mess') == ""


def test_format_chunk_mixed_valid_and_malformed_keeps_only_valid() -> None:
    """One malformed line in the middle doesn't abort the whole chunk
    (regression guard — pre-filter behavior was a hard json.loads with
    no exception handler, which would raise mid-iteration)."""
    payload = (
        _ev(type="user", message={"role": "user",
            "content": [{"type": "text", "text": "first valid"}]},
            timestamp="2026-06-24T00:00:00Z") + "\n"
        + "garbage middle line\n"
        + _ev(type="assistant", message={"role": "assistant",
              "content": [{"type": "text", "text": "second valid"}]},
              timestamp="2026-06-24T00:00:01Z") + "\n"
    )
    out = format_chunk(payload)
    assert "first valid" in out
    assert "second valid" in out
    assert "garbage middle line" not in out


# --------------------------------------------------------------------------- #
# §B — Per-event-type dispatch
# --------------------------------------------------------------------------- #
def test_format_chunk_queue_operation_compressed_to_one_line() -> None:
    payload = _ev(type="queue-operation", operation="enqueue",
                  content="Reply with READY",
                  timestamp="2026-06-24T00:00:00Z") + "\n"
    out = format_chunk(payload)
    assert "queue-operation: enqueue" in out
    assert "Reply with READY" in out


def test_format_chunk_attachment_collapses_to_marker() -> None:
    payload = _ev(type="attachment", timestamp="2026-06-24T00:00:00Z",
                  data="<lots of bytes here>" * 100) + "\n"
    out = format_chunk(payload)
    assert "attachment" in out
    assert "<lots of bytes" not in out  # attachment body fully suppressed


def test_format_chunk_last_prompt_marker_one_line() -> None:
    payload = _ev(type="last-prompt", lastPrompt="hello",
                  timestamp="2026-06-24T00:00:00Z") + "\n"
    out = format_chunk(payload)
    assert "last-prompt marker" in out


def test_format_chunk_user_turn_renders_text_block_with_marker() -> None:
    payload = _ev(type="user",
                  message={"role": "user",
                           "content": [{"type": "text", "text": "what's the bug?"}]},
                  timestamp="2026-06-24T00:00:00Z") + "\n"
    out = format_chunk(payload)
    assert "USER" in out  # role header
    assert "[text] what's the bug?" in out


def test_format_chunk_assistant_turn_renders_tool_use_and_thinking() -> None:
    payload = _ev(type="assistant",
                  message={"role": "assistant", "model": "claude-haiku-4-5",
                           "content": [
                               {"type": "thinking", "thinking": "let me grep"},
                               {"type": "tool_use", "name": "Grep",
                                "input": {"pattern": "foo"}},
                           ]},
                  timestamp="2026-06-24T00:00:01Z") + "\n"
    out = format_chunk(payload)
    assert "ASSISTANT" in out
    assert "claude-haiku-4-5" in out
    assert "[thinking] let me grep" in out
    assert "[tool_use: Grep]" in out
    assert "foo" in out  # input rendered


def test_format_chunk_tool_result_is_error_flag_surfaced() -> None:
    payload = _ev(type="user",
                  message={"role": "user",
                           "content": [{"type": "tool_result", "is_error": True,
                                        "content": "boom"}]},
                  timestamp="2026-06-24T00:00:00Z") + "\n"
    out = format_chunk(payload)
    assert "[tool_result (is_error)]" in out
    assert "boom" in out


# --------------------------------------------------------------------------- #
# §C — Limit / truncation
# --------------------------------------------------------------------------- #
def test_format_chunk_default_limit_zero_no_truncation() -> None:
    """The daydream engine passes limit=0 by default (DREAM_PARSER_LIMIT
    defaults to '0' which parses to 0); matches the parser's CLI 'full' mode."""
    long_text = "x" * 2000
    payload = _ev(type="user",
                  message={"role": "user",
                           "content": [{"type": "text", "text": long_text}]},
                  timestamp="2026-06-24T00:00:00Z") + "\n"
    out = format_chunk(payload, limit=0)
    assert long_text in out  # full text round-trips
    assert "truncated" not in out


def test_format_chunk_positive_limit_truncates_with_marker() -> None:
    long_text = "x" * 2000
    payload = _ev(type="user",
                  message={"role": "user",
                           "content": [{"type": "text", "text": long_text}]},
                  timestamp="2026-06-24T00:00:00Z") + "\n"
    out = format_chunk(payload, limit=200)
    assert "truncated" in out
    assert "2000 chars total" in out
    assert long_text not in out  # full text NOT in output


# --------------------------------------------------------------------------- #
# §D — Round-trip noise-reduction sanity (regression guard for ratio)
# --------------------------------------------------------------------------- #
def test_format_chunk_compresses_pure_noise_chunk() -> None:
    """A chunk containing only queue-operations + attachments should compress
    dramatically — those are the canonical noise event-types ADR-026 targets."""
    raw_lines = []
    for i in range(20):
        raw_lines.append(_ev(type="queue-operation", operation="enqueue",
                             content="x" * 200,
                             timestamp="2026-06-24T00:00:00Z"))
        raw_lines.append(_ev(type="attachment",
                             data="y" * 500,
                             timestamp="2026-06-24T00:00:00Z"))
    raw = "\n".join(raw_lines) + "\n"
    out = format_chunk(raw)
    # Sanity: at least 30% noise reduction on this synthetic worst-case.
    assert len(out) < len(raw) * 0.7, f"expected compression, got ratio {len(out)/len(raw):.2%}"
    # All attachments collapsed to marker lines (no payload bytes)
    assert "y" * 500 not in out
