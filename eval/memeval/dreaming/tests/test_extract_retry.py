"""Validate-retry on bad JSON (suggestion1.md idea 5).

`_loads_lenient` already recovers fenced / prose-wrapped JSON; idea 5 adds ONE
corrective re-prompt for the residual failure mode — genuinely invalid / truncated
JSON — so a paid extraction isn't dropped to None. Proven here on the failure path
it targets (the fixture A/B never triggers it: those models emit valid JSON).
"""

from __future__ import annotations

from memeval.dreaming._extract import _parse_validate, extract_memories
from memeval.dreaming.llm import Completion
from memeval.dreaming.redaction import redact

_VALID = (
    '{"memories":[{"content":"When an LLM returns invalid JSON, retry once before '
    'dropping the chunk.","type":"Strategy","keywords":["json","retry"]}],'
    '"rejected":[]}'
)


class _StubClient:
    model = "stub"

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = 0

    def complete(self, prompt, *, system, max_tokens):
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return Completion(text=text, tokens_in=1, tokens_out=1)


def _idg():
    _idg.n += 1
    return f"m-{_idg.n}"


_idg.n = 0


def _extract(client):
    return extract_memories(
        redact("a coding transcript"), client=client,
        session_id="s", now=1.0, id_gen=_idg, max_tokens=64,
    )


def test_parse_validate_shapes():
    assert _parse_validate('{"memories":[]}')[0] == {"memories": []}
    assert _parse_validate("not json at all")[0] is None
    assert _parse_validate("[]")[0] is None          # not a dict
    assert _parse_validate('{"x":1}')[0] is None     # missing memories
    assert _parse_validate('{"memories":"x"}')[0] is None  # memories not list


def test_retry_recovers_invalid_then_valid():
    c = _StubClient(["this is not json {{{", _VALID])
    items = _extract(c)
    assert items is not None and len(items) == 1
    assert c.calls == 2  # first failed, retry succeeded


def test_no_retry_when_first_is_valid():
    c = _StubClient([_VALID, "unused"])
    items = _extract(c)
    assert items is not None and len(items) == 1
    assert c.calls == 1  # no wasted retry


def test_retry_disabled_drops_chunk(monkeypatch):
    monkeypatch.setenv("DREAM_EXTRACT_RETRY", "0")
    c = _StubClient(["not json", _VALID])
    items = _extract(c)
    assert items is None      # dropped, exactly as before idea 5
    assert c.calls == 1       # retry never attempted
