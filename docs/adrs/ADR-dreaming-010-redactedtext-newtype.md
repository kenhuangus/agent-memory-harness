---
id: ADR-dreaming-010
domain: dreaming
title: RedactedText NewType — structural enforcement of the redaction trust boundary
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — informed (LLMClient consumer)
origin: design session 2026-06-21 (halliday adversarial review of dreaming ADRs 004-009, Finding #1)
---

# ADR-dreaming-010: `RedactedText` NewType — structural enforcement of the redaction trust boundary

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

## Context
[`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md) policy: *"every
string Daydream passes to `LLMClient.complete()` … routes through `redact()`
first."* [`ADR-dreaming-006`](ADR-dreaming-006-llmclient-completion-dataclass.md)
gives `complete()` the signature `complete(prompt: str, *, system: str | None
= None, max_tokens: int = ...)`.

Halliday's review (2026-06-21) flagged this as the highest-risk gap in the
v1 ADR set: **the trust-boundary guarantee is enforced by caller discipline,
not by a type-system property.** The next contributor adds a new
prompt-construction site, forgets to call `redact()` on `system=...`, and the
leak ships silently — no test, no review, no type catches it. The
events-stream tripwire ([`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md))
only fires *after* a regex match, so anything novel slips by undetected.

## Options considered
- **Type-system enforcement via NewType** (chosen) — `RedactedText` is a
  `typing.NewType` wrapping `str`; `redact()` is its only producer; every
  LLMClient signature requires `RedactedText`. mypy enforces the
  guarantee.
- Linter rule that flags `LLMClient.complete()` calls not preceded by
  `redact()` — fragile, false positives, custom tooling we'd maintain.
- Runtime check inside `complete()` (call `redact()` again defensively) —
  wastes OpenRouter tokens scanning already-redacted text; doesn't catch
  the *system*-prompt case meaningfully because the runtime check IS
  `redact()`, so we just paid twice.
- Status quo prose-only contract — rejected; halliday's finding.

## Decision
Introduce a NewType in the dreaming module:

```python
from typing import NewType

RedactedText = NewType("RedactedText", str)
```

Update signatures:

- `redact(text: str) -> RedactedText` is the **only** function that
  produces `RedactedText`. `redact()`'s body ends with `return
  RedactedText(cleaned)`.
- `LLMClient.complete(prompt: RedactedText, *, system: RedactedText | None
  = None, max_tokens: int = ...) -> Completion` — updates
  [`ADR-dreaming-006`](ADR-dreaming-006-llmclient-completion-dataclass.md)'s
  parameter types. Return type unchanged.

CI runs `mypy --strict` on `eval/memeval/dreaming/`. Any attempt to pass a
raw `str` into `complete()` fails type-check.

## Rationale
mypy enforcement catches the failure mode that prose can't. Cost is one
NewType (zero runtime overhead — `NewType` is a type-checker fiction) plus
a one-time signature change to the LLMClient interface (no existing
consumers in tree — ADR-006 hasn't been implemented yet, so cost is the
ADR text alone). Test surface: a single mypy CI invocation replaces "every
call site reviewed for discipline" — the failure mode that depended on
human attention becomes a build break.

The escape hatch (`RedactedText("hardcoded literal")`) is intentionally
visible: anyone constructing one without `redact()` is *explicitly opting
out* of the guarantee, which reads as a SECURITY-grade code smell in
review rather than a forgotten step.

## Tradeoffs & risks
- **Adds a wrapping concept consumers learn.** Small — `RedactedText` is
  a `str` at runtime; only mypy distinguishes.
- **mypy must run in CI for the enforcement to bite.** Eval CI is Ken's
  domain; coordination needed to add the strict-mypy step. Without it,
  the contract degrades to prose again.
- **Test fixtures need explicit casts.** `RedactedText("...")` in tests is
  the convention; lints should not flag it.
- **Bypass via explicit cast** is possible. Acceptable — bypasses are
  visible in review; the slope is "deliberately bypassing the safety," not
  "forgot to call redact()."
- **Cross-owner change to ADR-006's signature.** Keith co-owns
  ADR-003/006's client interface — explicit review request on PR #31.

## Consequences for the build
- **Contract — source of truth:** the `RedactedText` NewType in the
  dreaming module's redaction package (e.g.
  `eval/memeval/dreaming/redaction/__init__.py`).
- **Shape:**
  ```python
  RedactedText = NewType("RedactedText", str)

  def redact(text: str) -> RedactedText: ...

  class LLMClient(Protocol):
      def complete(
          self, prompt: RedactedText, *,
          system: RedactedText | None = None,
          max_tokens: int = ...,
      ) -> Completion: ...
  ```
- **Policy — every prompt-construction site** in Daydream and Dreaming
  produces a `RedactedText` via `redact()`. Casting (`RedactedText(s)`)
  is reserved for tests and for documented deliberate bypasses (which
  the reviewer must approve).
- **Policy — `system` parameter** of `complete()` is also `RedactedText`
  (system prompts can leak just as easily; the guarantee covers both).
- **Policy — CI runs `mypy --strict`** on `eval/memeval/dreaming/`. A
  test that confirms a `str → complete()` call fails type-check ships
  with the redaction module (negative-typecheck test).
- **Exhaustive consumers** of `RedactedText`: Daydream chunk extraction,
  Dreaming whole-store consolidation, any future caller of
  `LLMClient.complete()`.

## Open items (dreaming-owned + cross-domain)
- **Mypy in CI** — coordinate with Ken (eval CI owner) to add a
  `mypy --strict eval/memeval/dreaming/` step. Without it, this ADR's
  enforcement is aspirational.
- **Convention for test casts** — short docstring/convention note: tests
  use `RedactedText("...")` with an inline comment naming the test purpose.
- **Future audit** — once the redaction module ships, grep for
  `RedactedText(` outside `redact()` and tests. Anything that turns up
  is a deliberate bypass that should justify itself in code review.
