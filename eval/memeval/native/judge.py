"""Pluggable judge for native scoring — deterministic offline, LLM online.

LongMemEval is the only one of the five benchmarks whose paper grades with an
LLM-as-judge (GPT-4o by default). Every other benchmark scores deterministically
(SubEM / EM / test-pass / set-overlap), so the judge seam exists mainly for
LongMemEval but is generic enough for any yes/no correctness call.

Two implementations behind one :class:`Judge` Protocol:

* :class:`DeterministicJudge` — offline, stdlib-only, no network. It stands in
  for the LLM judge with normalized string / substring / token-overlap matching,
  multiple-choice option matching, and (for abstention items) refusal-phrase
  detection. Reuses :func:`memeval.metrics.normalize_answer` so its
  canonicalization matches the rest of the harness. This is the default and is
  what the offline tests use.
* :class:`AnthropicJudge` — the live judge. Lazy-imports ``anthropic`` ONLY when
  ``judge()`` is first called, so importing this module (and the whole offline
  path) needs no extra deps. It builds a yes/no prompt in the spirit of
  LongMemEval's ``get_anscheck_prompt`` and parses ``"yes" in response.lower()``.

The judge interface
-------------------
``judge(question, gold, prediction, *, kind) -> bool`` returns ``True`` when the
prediction is judged correct for the given ``kind``:

* ``kind="qa"`` (default) — prediction answers the question per ``gold``.
* ``kind="abstention"`` — prediction correctly identifies the question as
  unanswerable (``gold`` carries the unanswerable explanation; offline this is a
  refusal-phrase detector over the prediction).
* ``kind="preference"`` — single-session-preference: ``gold`` is a rubric; the
  deterministic path falls back to overlap against the rubric text.
* ``kind="choice"`` — multiple-choice: ``gold`` is the correct option/letter;
  matched exactly (after light canonicalization).

The boolean return keeps the contract simple; a graded score in ``[0,1]`` can be
obtained by averaging booleans across items.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional, Protocol, runtime_checkable

from ..metrics import normalize_answer


# --------------------------------------------------------------------------- #
# Judge Protocol
# --------------------------------------------------------------------------- #
@runtime_checkable
class Judge(Protocol):
    """A yes/no correctness judge for native QA scoring.

    ``name`` identifies the judge in report metadata. ``judge`` returns ``True``
    iff the prediction is correct for the question/gold under the given ``kind``.
    """

    name: str

    def judge(
        self,
        question: str,
        gold: str,
        prediction: str,
        *,
        kind: str = "qa",
    ) -> bool:
        """Return whether ``prediction`` is correct for ``question``/``gold``."""
        ...


# --------------------------------------------------------------------------- #
# Refusal / abstention detection (offline)
# --------------------------------------------------------------------------- #
#: Phrases that signal a model correctly abstained ("I don't know", "cannot
#: determine", "no information", ...). Matched case-insensitively as substrings
#: of the NORMALIZED prediction so punctuation/casing don't matter.
_REFUSAL_PHRASES: tuple[str, ...] = (
    "i don t know",          # normalize_answer strips the apostrophe -> "don t"
    "i dont know",
    "do not know",
    "don t know",
    "not know",
    "cannot answer",
    "can not answer",
    "cannot determine",
    "can not determine",
    "cannot be determined",
    "unable to answer",
    "unable to determine",
    "no information",
    "not enough information",
    "insufficient information",
    "not mentioned",
    "not specified",
    "not provided",
    "not available",
    "no record",
    "no mention",
    "isn t mentioned",
    "wasn t mentioned",
    "there is no",
    "i m not sure",
    "im not sure",
    "not sure",
    "unanswerable",
    "cannot be answered",
    "can not be answered",
    "no relevant",
    "incomplete",
    "insufficient",
)


def looks_like_refusal(prediction: str) -> bool:
    """Heuristic: does ``prediction`` correctly abstain / say it can't answer?

    Operates on :func:`memeval.metrics.normalize_answer` output so casing,
    punctuation, and the articles ``a/an/the`` are removed first. Used by the
    deterministic abstention judge. Empty prediction is NOT a refusal (an empty
    answer is a miss, not a deliberate abstention).
    """
    norm = normalize_answer(prediction)
    if not norm:
        return False
    return any(p in norm for p in _REFUSAL_PHRASES)


# --------------------------------------------------------------------------- #
# DeterministicJudge — offline stdlib stand-in
# --------------------------------------------------------------------------- #
class DeterministicJudge:
    """Offline, deterministic stand-in for the LLM judge (stdlib only).

    Scoring by ``kind``:

    * ``"qa"`` / ``"preference"`` / default — correct iff the normalized gold is
      a contiguous whole-word run inside the normalized prediction (the same
      tolerance as :func:`memeval.metrics.qa_match`) OR token-overlap of gold vs
      prediction meets ``overlap_threshold`` (handles fuller-sentence and
      reordered answers). For ``"preference"`` the ``gold`` rubric is matched by
      overlap only (a rubric is rarely a verbatim substring).
    * ``"choice"`` — strict equality of the canonicalized prediction and gold
      option (no substring tolerance), so ``"label: 43"`` ≠ ``"43"`` only when
      the surrounding text differs; a chosen letter/option must match exactly.
    * ``"abstention"`` — correct iff :func:`looks_like_refusal` fires on the
      prediction (``gold`` = the unanswerable explanation, used only as context).

    Deterministic given its inputs; no randomness, no network, no LLM.
    """

    name = "deterministic"

    def __init__(self, *, overlap_threshold: float = 0.6) -> None:
        self.overlap_threshold = overlap_threshold

    # -- public API ------------------------------------------------------- #
    def judge(
        self,
        question: str,
        gold: str,
        prediction: str,
        *,
        kind: str = "qa",
    ) -> bool:
        k = (kind or "qa").strip().lower()
        if k in ("abstention", "abs", "unanswerable"):
            return looks_like_refusal(prediction)
        if k in ("choice", "mcq", "multiple_choice", "multiple-choice"):
            return self._choice_match(gold, prediction)
        if k in ("preference", "pref", "single-session-preference"):
            # Rubrics are descriptive, not verbatim answers: overlap only.
            return self._overlap(gold, prediction) >= self.overlap_threshold
        # Default QA: substring tolerance OR overlap.
        return self._qa_match(gold, prediction) or (
            self._overlap(gold, prediction) >= self.overlap_threshold
        )

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _qa_match(gold: str, prediction: str) -> bool:
        """Whole-word contiguous containment of gold inside prediction."""
        ng = normalize_answer(gold).split()
        npred = normalize_answer(prediction).split()
        if not ng:
            return not npred
        if ng == npred:
            return True
        n = len(ng)
        for start in range(len(npred) - n + 1):
            if npred[start:start + n] == ng:
                return True
        return False

    @staticmethod
    def _choice_match(gold: str, prediction: str) -> bool:
        """Strict canonicalized equality for multiple-choice options."""
        g = normalize_answer(gold)
        p = normalize_answer(prediction)
        if not g:
            return not p
        # Exact, or the gold option appears as a standalone whole-word token in
        # the prediction (covers "Answer: B" style without substring leakage).
        if g == p:
            return True
        return g in p.split()

    def _overlap(self, gold: str, prediction: str) -> float:
        """Token-overlap recall of gold in prediction (|g∩p| / |g|)."""
        gt = set(normalize_answer(gold).split())
        pt = set(normalize_answer(prediction).split())
        if not gt:
            return 1.0 if not pt else 0.0
        return len(gt & pt) / len(gt)


# --------------------------------------------------------------------------- #
# AnthropicJudge — live LLM judge (lazy anthropic import)
# --------------------------------------------------------------------------- #
# LongMemEval's grader prompt families, paraphrased into one parameterized
# template. The live path still parses correctness as ``"yes" in resp.lower()``,
# matching the official ``evaluate_qa.py`` label parse.
_JUDGE_PROMPTS: dict[str, str] = {
    "qa": (
        "I will give you a question, a correct answer, and a response from a "
        "model. Decide whether the response contains the correct answer.\n\n"
        "Question: {question}\nCorrect answer: {gold}\nResponse: {prediction}\n\n"
        "Does the response contain the correct answer? Answer yes or no."
    ),
    "preference": (
        "I will give you a question, a rubric for the ideal answer, and a "
        "response from a model. Decide whether the response satisfies the "
        "rubric.\n\nQuestion: {question}\nRubric: {gold}\nResponse: {prediction}"
        "\n\nDoes the response satisfy the rubric? Answer yes or no."
    ),
    "abstention": (
        "I will give you an UNANSWERABLE question and a model's response. The "
        "model is correct only if it recognizes the question cannot be answered "
        "from the available information (e.g. states the info is missing or "
        "insufficient).\n\nQuestion: {question}\nWhy it is unanswerable: {gold}\n"
        "Response: {prediction}\n\nDoes the response correctly identify the "
        "question as unanswerable? Answer yes or no."
    ),
    "choice": (
        "I will give you a multiple-choice question, the correct option, and a "
        "model's response. Decide whether the response selects the correct "
        "option.\n\nQuestion: {question}\nCorrect option: {gold}\nResponse: "
        "{prediction}\n\nIs the response correct? Answer yes or no."
    ),
}


class AnthropicJudge:
    """Live LLM judge over the Anthropic SDK (lazy import; online only).

    Builds a yes/no correctness prompt (LongMemEval-style) per ``kind`` and
    returns ``"yes" in response.lower()`` — the same label parse the official
    grader uses. The ``anthropic`` package is imported only inside
    :meth:`judge`, so this module imports with the standard library alone.

    Parameters
    ----------
    model:
        Judge model id (default ``"claude-sonnet-4-5"``; the paper uses GPT-4o,
        any capable judge model works since the parse is the same yes/no).
    api_key / api_key_env:
        Explicit key, or the env var to read it from (default
        ``ANTHROPIC_API_KEY``).
    """

    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        *,
        api_key: Optional[str] = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        max_tokens: int = 16,
    ) -> None:
        self.model = model
        self.name = f"anthropic:{model}"
        self.api_key_env = api_key_env
        self._api_key = api_key if api_key is not None else os.environ.get(api_key_env)
        self.max_tokens = max_tokens
        self._client: Any = None  # constructed lazily on first judge()

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - online only
                raise ImportError(
                    "AnthropicJudge requires the 'anthropic' package. Install "
                    "with `pip install anthropic` (optional dependency; the "
                    "offline DeterministicJudge path needs no extra deps)."
                ) from exc
            self._client = (
                anthropic.Anthropic(api_key=self._api_key)
                if self._api_key
                else anthropic.Anthropic()
            )
        return self._client

    def judge(
        self,
        question: str,
        gold: str,
        prediction: str,
        *,
        kind: str = "qa",
    ) -> bool:  # pragma: no cover - online only
        k = (kind or "qa").strip().lower()
        template = _JUDGE_PROMPTS.get(k, _JUDGE_PROMPTS["qa"])
        prompt = template.format(
            question=question or "", gold=gold or "", prediction=prediction or ""
        )
        client = self._ensure_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = []
        for block in getattr(resp, "content", []) or []:
            btext = getattr(block, "text", None)
            if btext is not None:
                parts.append(btext)
        return "yes" in "".join(parts).strip().lower()


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
_DETERMINISTIC_SPECS = {"deterministic", "det", "offline", "echo", "none", ""}


def get_judge(spec: "str | Judge | None" = None, **kwargs: Any) -> Judge:
    """Resolve a judge spec into a concrete :class:`Judge`.

    * ``None`` / ``"deterministic"`` (and aliases) -> :class:`DeterministicJudge`.
    * an already-constructed :class:`Judge` -> returned unchanged.
    * any other string -> treated as an Anthropic judge model id and wrapped in
      :class:`AnthropicJudge` (lazy-imports ``anthropic`` only when used).

    Keeps the offline path dependency-free; only a real judge model id pulls in
    ``anthropic``.
    """
    if spec is None:
        return DeterministicJudge(**kwargs)
    if hasattr(spec, "judge") and not isinstance(spec, str):
        return spec  # type: ignore[return-value]
    if isinstance(spec, str):
        if spec.strip().lower() in _DETERMINISTIC_SPECS:
            return DeterministicJudge(**kwargs)
        return AnthropicJudge(spec, **kwargs)
    raise TypeError(f"Cannot resolve judge spec of type {type(spec)!r}: {spec!r}")


__all__ = [
    "Judge",
    "DeterministicJudge",
    "AnthropicJudge",
    "get_judge",
    "looks_like_refusal",
]
