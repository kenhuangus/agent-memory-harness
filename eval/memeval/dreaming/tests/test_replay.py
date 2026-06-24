"""Tests for the daydream replay CLI — `memeval.dreaming.replay.cli`.

Deterministic tests use a local `_StubLLMClient` (mirrors test_engine.py's
StubClient — minimal LLMClient protocol implementation returning canned
completion text). The REAL engine.daydream is invoked end-to-end; only the
LLM seam is stubbed. This is the only way to exercise the cursor-advance
ordering + fail-open boundary + diary-event ordering that the replay tool
depends on. Monkeypatching engine.daydream itself would let regressions in
those load-bearing behaviors slip through (adversarial-test-discipline
finding on the design spec).

A live-LLM smoke test exists at the end, gated by BOTH
``DREAM_TESTS_ALLOW_LIVE_LLM=1`` AND ``OPENROUTER_API_KEY`` set — same
discipline as the rest of the dreaming suite (conftest.py).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from memeval.dreaming.llm import Completion, LLMClient
from memeval.dreaming.redaction import RedactedText
from memeval.dreaming.replay.cli import (
    Slice,
    classify_outcome,
    main,
    replay_fixtures,
    slice_fixture,
)
from memeval.harness import InMemoryStore


# --------------------------------------------------------------------------- #
# Stubs + fixtures
# --------------------------------------------------------------------------- #
class _StubLLMClient:
    """Deterministic LLMClient — mirrors test_engine.py:StubClient shape."""

    def __init__(
        self,
        *,
        text: str = '{"memories": [], "rejected": []}',
        tokens_in: int = 10,
        tokens_out: int = 20,
        model: str = "echo",
    ) -> None:
        self.model = model
        self._text = text
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self.calls: list[tuple[Any, Any, int]] = []

    def complete(
        self,
        prompt: RedactedText,
        *,
        system: RedactedText | None = None,
        max_tokens: int = 4096,
    ) -> Completion:
        self.calls.append((prompt, system, max_tokens))
        return Completion(
            text=self._text, tokens_in=self._tokens_in, tokens_out=self._tokens_out
        )


class _SequenceLLMClient:
    """Scripted completions per call — feeds different behavior per slice."""

    model = "echo"

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.calls = 0

    def complete(
        self, prompt: RedactedText, *, system: RedactedText | None = None,
        max_tokens: int = 4096,
    ) -> Completion:
        i = min(self.calls, len(self._texts) - 1)
        self.calls += 1
        return Completion(text=self._texts[i], tokens_in=10, tokens_out=20)


def _ok_completion_text(memories: list[dict[str, Any]]) -> str:
    return json.dumps({"memories": memories, "rejected": []})


@pytest.fixture
def fixture_5_lines(tmp_path: Path) -> Path:
    """Hand-crafted CC-shaped fixture: 5 JSONL lines, each ~200 bytes."""
    p = tmp_path / "fix-5lines.jsonl"
    lines = [
        # Real-ish CC native-shape events; intentionally compact so chunk-byte
        # tests have predictable sizes.
        '{"type":"user","message":{"role":"user","content":"hello"},"sessionId":"s","uuid":"u1","timestamp":"2026-06-24T00:00:00Z"}',
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hi"}]},"sessionId":"s","uuid":"u2","timestamp":"2026-06-24T00:00:01Z"}',
        '{"type":"user","message":{"role":"user","content":"again"},"sessionId":"s","uuid":"u3","timestamp":"2026-06-24T00:00:02Z"}',
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"sure"}]},"sessionId":"s","uuid":"u4","timestamp":"2026-06-24T00:00:03Z"}',
        '{"type":"user","message":{"role":"user","content":"bye"},"sessionId":"s","uuid":"u5","timestamp":"2026-06-24T00:00:04Z"}',
    ]
    p.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return p


@pytest.fixture
def basedir(tmp_path: Path) -> Path:
    bd = tmp_path / "memstore_base"
    (bd / "dream").mkdir(parents=True, exist_ok=True)
    return bd


# --------------------------------------------------------------------------- #
# §A — slice_fixture (pure-function tests)
# --------------------------------------------------------------------------- #
def test_slice_lines_whole_line_alignment(fixture_5_lines: Path) -> None:
    """Chunking actually happens (>1 slice) AND every slice is whole valid JSONL.

    Asserts behavior, not exact partition — the partition shifts on small
    line-size differences between fixture lines (asymmetric content lengths
    are inherent to claude-code transcripts). The conservation test
    (`test_slice_conservation_*`) already pins the byte invariants.
    """
    slices = list(
        slice_fixture(fixture_5_lines, chunk_bytes=300, whole_fixture=False)
    )
    assert len(slices) > 1, "chunk_bytes=300 with a 5-line fixture should produce >1 slice"
    # Every emitted slice's bytes parse as whole JSONL lines (no torn JSON).
    for s in slices:
        for line in s.bytes_.splitlines():
            json.loads(line)
    # Line counts sum to the full fixture line count, no lines lost.
    total_lines = fixture_5_lines.read_bytes().count(b"\n")
    assert sum(s.line_end - s.line_start for s in slices) == total_lines


def test_slice_lines_oversized_single_line(tmp_path: Path) -> None:
    """A single line >chunk_bytes is emitted alone; size > chunk_bytes is surfaced."""
    p = tmp_path / "big.jsonl"
    huge = '{"type":"assistant","data":"' + ("x" * 80_000) + '"}\n'
    p.write_bytes(huge.encode())
    slices = list(slice_fixture(p, chunk_bytes=50_000, whole_fixture=False))
    assert len(slices) == 1
    assert slices[0].size == len(huge.encode())
    assert slices[0].size > 50_000  # oversized — exposed to caller, not hidden


def test_slice_conservation_byte_equal_to_fixture(fixture_5_lines: Path) -> None:
    """Concatenating every slice's bytes in order MUST equal the fixture file bytes.

    Catches off-by-one byte tracking or silent line drops in the slicer.
    """
    slices = list(slice_fixture(fixture_5_lines, chunk_bytes=300, whole_fixture=False))
    reconstructed = b"".join(s.bytes_ for s in slices)
    assert reconstructed == fixture_5_lines.read_bytes()
    # Byte ranges are contiguous and cover the whole file.
    assert slices[0].byte_start == 0
    assert slices[-1].byte_end == fixture_5_lines.stat().st_size
    for a, b in zip(slices, slices[1:]):
        assert a.byte_end == b.byte_start


def test_slice_whole_fixture_one_slice(fixture_5_lines: Path) -> None:
    """--whole-fixture yields exactly one slice covering the entire file."""
    slices = list(slice_fixture(fixture_5_lines, chunk_bytes=50, whole_fixture=True))
    assert len(slices) == 1
    assert slices[0].size == fixture_5_lines.stat().st_size
    assert slices[0].byte_start == 0
    assert slices[0].byte_end == fixture_5_lines.stat().st_size


def test_slice_empty_fixture_yields_nothing(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_bytes(b"")
    assert list(slice_fixture(p, chunk_bytes=1000, whole_fixture=False)) == []
    assert list(slice_fixture(p, chunk_bytes=1000, whole_fixture=True)) == []


def test_slice_invalid_chunk_bytes_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    p.write_bytes(b"x\n")
    with pytest.raises(ValueError):
        list(slice_fixture(p, chunk_bytes=0, whole_fixture=False))


# --------------------------------------------------------------------------- #
# §B — classify_outcome (pure-function tests; table-driven)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("events,advanced,expected", [
    # Priority: chunk_error > lock_held > parse_failed > unavailable_llm > memory_written
    ({"daydream.chunk_error", "daydream.memory_written"}, True, "chunk_error"),
    ({"daydream.dream_in_progress_skipped"}, False, "skipped_lock_held"),
    ({"chunk_skipped_parse_failed"}, False, "skipped_parse_failed"),
    ({"chunk_skipped_unavailable_llm"}, False, "skipped_llm_unavailable"),
    ({"daydream.memory_written"}, True, "wrote_memories"),
    (set(), True, "advanced_no_items"),
    (set(), False, "no_advance_no_event"),
    # Whitespace-only path: no events, cursor didn't advance — distinct from #133.
    (set(), False, "no_advance_no_event"),
])
def test_classify_outcome_priority(events, advanced, expected) -> None:
    assert classify_outcome(events, cursor_advanced=advanced) == expected


# --------------------------------------------------------------------------- #
# §C — End-to-end with REAL engine + stub LLM client
# --------------------------------------------------------------------------- #
def _read_jsonl(buf: io.StringIO) -> list[dict[str, Any]]:
    buf.seek(0)
    return [json.loads(line) for line in buf.read().splitlines() if line.strip()]


def test_replay_e2e_wrote_memories_happy_path(
    fixture_5_lines: Path, basedir: Path
) -> None:
    """Real engine + stub returning valid memories → outcome=wrote_memories,
    cursor advances, items_written tracked."""
    out = io.StringIO()
    client = _StubLLMClient(
        text=_ok_completion_text([
            {"content": "a fact", "tags": ["t"], "relevancy": 0.9}
        ])
    )
    rc = replay_fixtures(
        fixtures=[fixture_5_lines],
        basedir=basedir,
        chunk_bytes=200,
        whole_fixture=False,
        max_chunks=None,
        shared_session_id=None,
        client=client,
        store=InMemoryStore(),
        out=out,
    )
    assert rc == 0
    records = _read_jsonl(out)
    chunks = [r for r in records if r["event_type"] == "replay.chunk"]
    assert chunks, "no replay.chunk records emitted"
    for c in chunks:
        assert c["outcome"] == "wrote_memories"
        assert c["items_written"] >= 1
        assert c["cursor_after"] > c["cursor_before"]
    fix_sum = next(r for r in records if r["event_type"] == "replay.fixture_summary")
    assert fix_sum["chunks_advanced"] == fix_sum["chunks_total"]
    run_sum = next(r for r in records if r["event_type"] == "replay.run_summary")
    assert run_sum["issue_133_runs"] == []  # no stuck-cursor runs in happy path


def test_replay_e2e_stuck_cursor_fingerprints_issue_133(
    fixture_5_lines: Path, basedir: Path
) -> None:
    """Real engine + stub returning empty completion text → ADR-012 fail-open
    path → cursor does NOT advance + chunk_skipped_unavailable_llm fires.

    EVERY slice should classify as `skipped_llm_unavailable` with the SAME
    stuck cursor (0). The issue-#133 aggregator should collapse them into
    one run with `consecutive_skips == n_slices`.
    """
    out = io.StringIO()
    client = _StubLLMClient(text="")  # empty completion → ADR-012 unavailable
    rc = replay_fixtures(
        fixtures=[fixture_5_lines],
        basedir=basedir,
        chunk_bytes=200,
        whole_fixture=False,
        max_chunks=None,
        shared_session_id=None,
        client=client,
        store=InMemoryStore(),
        out=out,
    )
    assert rc == 0
    records = _read_jsonl(out)
    chunks = [r for r in records if r["event_type"] == "replay.chunk"]
    assert len(chunks) >= 2
    for c in chunks:
        assert c["outcome"] == "skipped_llm_unavailable", c
        assert c["cursor_after"] == c["cursor_before"] == 0
        assert c["items_written"] == 0
    run_sum = next(r for r in records if r["event_type"] == "replay.run_summary")
    assert len(run_sum["issue_133_runs"]) == 1, run_sum["issue_133_runs"]
    run = run_sum["issue_133_runs"][0]
    assert run["stuck_cursor"] == 0
    assert run["consecutive_skips"] == len(chunks)
    assert run["start_chunk"] == 0


def test_replay_e2e_stuck_run_splits_on_advance(
    fixture_5_lines: Path, basedir: Path
) -> None:
    """A mid-sequence happy completion CLOSES the prior stuck run and
    starts a fresh one when stuckness resumes — must not run them together."""
    # Script: empty, empty, OK, empty, empty → 2 distinct stuck runs around 1 ok.
    client = _SequenceLLMClient([
        "",
        "",
        _ok_completion_text([{"content": "x", "tags": [], "relevancy": 0.5}]),
        "",
        "",
    ])
    out = io.StringIO()
    rc = replay_fixtures(
        fixtures=[fixture_5_lines],
        basedir=basedir,
        chunk_bytes=200,
        whole_fixture=False,
        max_chunks=None,
        shared_session_id=None,
        client=client,
        store=InMemoryStore(),
        out=out,
    )
    assert rc == 0
    records = _read_jsonl(out)
    run_sum = next(r for r in records if r["event_type"] == "replay.run_summary")
    runs = run_sum["issue_133_runs"]
    # Two stuck runs, separated by the ok slice. The second run's stuck_cursor
    # MUST differ from the first's (ok advanced the cursor between them).
    assert len(runs) == 2, runs
    assert runs[0]["stuck_cursor"] != runs[1]["stuck_cursor"]
    assert runs[0]["consecutive_skips"] == 2
    assert runs[1]["consecutive_skips"] == 2


def test_replay_max_chunks_caps_slices(
    fixture_5_lines: Path, basedir: Path
) -> None:
    """--max-chunks N stops after N daydream calls regardless of fixture remaining."""
    out = io.StringIO()
    client = _StubLLMClient(text=_ok_completion_text([{"content": "x"}]))
    rc = replay_fixtures(
        fixtures=[fixture_5_lines],
        basedir=basedir,
        chunk_bytes=200,
        whole_fixture=False,
        max_chunks=2,
        shared_session_id=None,
        client=client,
        store=InMemoryStore(),
        out=out,
    )
    assert rc == 0
    records = _read_jsonl(out)
    chunks = [r for r in records if r["event_type"] == "replay.chunk"]
    assert len(chunks) == 2
    assert client.calls and len(client.calls) == 2


def test_replay_whole_fixture_one_call(
    fixture_5_lines: Path, basedir: Path
) -> None:
    """--whole-fixture feeds the ENTIRE fixture as one slice (the #133 reproducer)."""
    out = io.StringIO()
    client = _StubLLMClient(text=_ok_completion_text([{"content": "x"}]))
    rc = replay_fixtures(
        fixtures=[fixture_5_lines],
        basedir=basedir,
        chunk_bytes=50,  # ignored when whole_fixture=True
        whole_fixture=True,
        max_chunks=None,
        shared_session_id=None,
        client=client,
        store=InMemoryStore(),
        out=out,
    )
    assert rc == 0
    chunks = [r for r in _read_jsonl(out) if r["event_type"] == "replay.chunk"]
    assert len(chunks) == 1
    assert chunks[0]["boundary"] == "whole_fixture"
    assert chunks[0]["bytes_in_chunk"] == fixture_5_lines.stat().st_size
    assert len(client.calls) == 1


# --------------------------------------------------------------------------- #
# §D — CLI argv-level tests
# --------------------------------------------------------------------------- #
def test_main_aborts_when_openrouter_api_key_unset(
    monkeypatch, fixture_5_lines: Path, capsys
) -> None:
    """The CLI fails fast (exit 2) when OPENROUTER_API_KEY is unset.
    Silent fail-open here would mask the very symptom the tool diagnoses."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    rc = main(["--fixture", str(fixture_5_lines)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "OPENROUTER_API_KEY" in err


def test_main_aborts_on_missing_fixture(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """Mistyped --fixture path is caught up-front, not mid-replay."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-for-test")
    rc = main(["--fixture", str(tmp_path / "does-not-exist.jsonl")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# §E — Live-LLM smoke. Doubly-gated; intentionally narrow asserts.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not (
        os.environ.get("OPENROUTER_API_KEY")
        and os.environ.get("DREAM_TESTS_ALLOW_LIVE_LLM") == "1"
    ),
    reason="live LLM smoke requires OPENROUTER_API_KEY + DREAM_TESTS_ALLOW_LIVE_LLM=1",
)
def test_replay_live_smoke_on_smallest_fixture(basedir: Path) -> None:
    """Real OpenRouter call on the smallest committed fixture (~120KB).

    Asserts only the wire shape: replay completes, run_summary fires.
    Does NOT assert specific memory content (LLM non-determinism). Useful
    as a one-off smoke from the shell:
        OPENROUTER_API_KEY=... DREAM_TESTS_ALLOW_LIVE_LLM=1 \\
          pytest -k test_replay_live_smoke -s
    """
    from memeval.dreaming.llm import make_client

    fixtures_dir = (
        Path(__file__).parent.parent / "replay" / "fixtures"
    )
    smallest = fixtures_dir / "astropy_astropy_sequence-14.jsonl"
    if not smallest.is_file():
        pytest.skip(f"fixture missing: {smallest}")
    out = io.StringIO()
    rc = replay_fixtures(
        fixtures=[smallest],
        basedir=basedir,
        chunk_bytes=50_000,
        whole_fixture=False,
        max_chunks=2,  # cap cost
        shared_session_id=None,
        client=make_client(),
        store=InMemoryStore(),
        out=out,
    )
    assert rc == 0
    records = _read_jsonl(out)
    assert any(r["event_type"] == "replay.run_start" for r in records)
    assert any(r["event_type"] == "replay.run_summary" for r in records)
    chunks = [r for r in records if r["event_type"] == "replay.chunk"]
    assert chunks, "no replay.chunk records — replay aborted before slicing"
