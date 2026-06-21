---
id: ADR-dreaming-011
domain: dreaming
title: Expanded redaction scope — DB/URL-credential detectors + explicit out-of-scope + FP/FN measurement Policy
status: Accepted
date: 2026-06-21
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4)
origin: design session 2026-06-21 (halliday adversarial review of dreaming ADRs 004-009, Finding #5)
---

# ADR-dreaming-011: Expanded redaction scope — DB / URL-credential detectors + out-of-scope policy + FP/FN measurement

**Status:** Accepted · **Date:** 2026-06-21 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

> **Scope.** This ADR **amends**
> [`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md) in three
> specific places — (1) adds two custom plugins to the curated list, (2)
> adds an explicit out-of-scope policy, (3) promotes the FP/FN measurement
> "Open item" in ADR-005 to a Policy with concrete implementation. ADR-005
> otherwise stands. No supersession — pattern matches ADR-005's own
> partial-amend of ADR-harness-005.

## Context
[`ADR-dreaming-005`](ADR-dreaming-005-v1-inline-redaction.md) ships 11
structured `detect-secrets` plugins + 4 custom plugins (`AnthropicKeyDetector`,
`OpenRouterKeyDetector`, `GoogleCloudKeyDetector`, `BearerTokenDetector`).
Halliday (2026-06-21) flagged real exposure vectors the set misses:

1. **Database connection strings.** `postgres://user:pw@host/db`,
   `mysql://`, `mongodb://`, `redis://`, `amqp://` — the credential
   lives in the URL userinfo; no built-in `detect-secrets` plugin catches
   it. Tool-call results commonly include these (a `Read` of a `.env` or
   `database.yml`, the agent describing connection setup in chat).
2. **URL-embedded credentials.** Query parameters like
   `?access_token=...`, `?api_key=...`, `?auth=...` — appear in tool
   results from HTTP calls, oauth flows, and shell histories.

Beyond these closeable gaps, novel formats (custom MCP server tokens,
internal-only auth shapes) are out of scope by design — but the design has
no place that documents what's NOT covered, which leaves reviewers /
downstream users unable to scope their own threat model.

ADR-005's "False-positive rate measurement" was listed as an Open item
("instrument PR1 testing to count `[REDACTED:*]` spans per chunk"). That's
a measurement of *detected* secrets only — no visibility into *missed*
ones. The eval driver cannot compute FP/FN rate without before/after
sample pairs.

## Options considered
- **Amend ADR-005 via this ADR** (chosen) — adds plugins to the curated
  list, adds out-of-scope, promotes FP/FN to Policy with concrete audit
  file.
- Full supersede ADR-005 with ADR-011 — heavy hammer; ADR-005's other
  decisions (entropy exclusion, direct-drive over scan_line, fail-open,
  events emission, etc.) are unchanged.
- Defer until eval data reveals the gaps — risk: data shows the leak
  only *after* it happens. Halliday's finding is preventive, not reactive.

## Decision

### 1. Two custom plugins added to the v1 plugin set
- **`DatabaseURLDetector`** — regex
  `(postgres|postgresql|mysql|mongodb|redis|amqp)://[^:\s]+:[^@\s]+@` (catches
  scheme + userinfo + `@`).
- **`URLCredentialDetector`** — regex
  `[?&](access_token|api_key|auth|token|secret|password)=[^&\s]{6,}` (catches
  common query-param credential keys with non-trivial values).

Both inherit from `RegexBasedDetector` and live in
`eval/memeval/dreaming/redaction/plugins/` per ADR-005's policy. Brings
the custom-plugin count from 4 to **6**.

### 2. Explicit out-of-scope list for v1 redaction
Daydream-v1 redaction does **not** catch:

- **Free-form English credentials** ("my password is hunter2", "the API
  key is X"). No pattern detector; would require LLM-based detection,
  which contradicts "redact before LLM call."
- **Novel/custom token formats** (one-off MCP server tokens,
  experimental provider keys). Surface these via FP/FN measurement below
  and add detectors in successor ADRs when patterns repeat.
- **PII** (personal names, emails, addresses). Separate concern; deferred.
  See `docs/honcho-comparison.md` (locally on `honcho-research` branch)
  for the Presidio path if/when PII becomes load-bearing.

This list is a contract with downstream users: "if you have these in your
sessions, redaction will not catch them; layer your own controls."

### 3. FP/FN measurement promoted from Open item to Policy
Daydream writes a per-chunk audit pair to a **local-only, gitignored**
file at `${MEMORY_STORE%/*}/dream/<session_id>.redact-audit.jsonl`. Each
line:

```json
{
  "ts": <unix>,
  "chunk_id": <int>,
  "pre": "<raw text>",
  "post": "<redacted text>",
  "detected": {"AWSKey": 1, "AnthropicKey": 0, ...}
}
```

This file:
- Is **never** transmitted anywhere (never read by the LLM, never
  uploaded). It is on-disk audit for the local operator's eval driver.
- Is gitignored (`*.redact-audit.jsonl` pattern).
- Subject to the same retention TTL as the events diary
  ([`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md) open item).
- Read by the eval driver (Ken's lane) to compute FP/FN rates over a
  sample.

## Rationale
DatabaseURLDetector and URLCredentialDetector close two real exposure
paths halliday named — both are common in coding-agent session content
and neither is caught by `detect-secrets` defaults. The out-of-scope list
turns "best-effort, accept residual risk" from a vague hedge into a
named, reviewable contract. FP/FN measurement closes the visibility loop
— without pre/post pairs, we can't tell whether the "best effort" is
actually working.

The audit file is local-only and gitignored because it contains
pre-redacted (potentially secret-bearing) text; the same reasoning that
keeps secrets out of OpenRouter keeps them out of git.

## Tradeoffs & risks
- **Two more plugins to maintain** and eventually upstream. Marginal.
- **Audit file is another per-session file** on disk; pairs with the
  events diary; same retention work covers both.
- **Audit file contains raw pre-redact text** — must never ship via
  gitignore (enforced) + same TTL as events diary (TBD; tracked as Open
  item paired with finding #8 from halliday's review).
- **Eval driver coupling** — FP/FN computation requires Ken's eval
  driver to read the audit file. Cross-domain dependency, but read-only
  on Ken's side; no risk of contention.

## Consequences for the build
- **Contract — source of truth:** the curated plugin list in the
  dreaming redaction module now includes 6 custom plugins (Anthropic,
  OpenRouter, GoogleCloud, BearerToken, DatabaseURL, URLCredential)
  alongside the 11 structured `detect-secrets` plugins from ADR-005 §1.
- **Shape — new custom plugins** follow the existing pattern:
  ```python
  class DatabaseURLDetector(RegexBasedDetector):
      secret_type = "Database Connection String"
      denylist = [re.compile(r"(postgres|postgresql|mysql|mongodb|redis|amqp)://[^:\s]+:[^@\s]+@")]

  class URLCredentialDetector(RegexBasedDetector):
      secret_type = "URL-Embedded Credential"
      denylist = [re.compile(r"[?&](access_token|api_key|auth|token|secret|password)=[^&\s]{6,}")]
  ```
- **Policy — out-of-scope list** is documented in two specific places
  so it's discoverable both ways:
  1. The module docstring at the top of
     `eval/memeval/dreaming/redaction/__init__.py`.
  2. The component-scoped README at
     `eval/memeval/dreaming/redaction/README.md` (created by the PR1
     scaffold; was undecided when this ADR was first written, now
     pinned to the component-scoped path so consumers see it next to
     the code).
- **Policy — audit file path:**
  - **Conceptual shape:** `<basedir>/dream/<session_id>.redact-audit.jsonl`
    where `<basedir>` is derived from `$MEMORY_STORE`.
  - **Concrete Python resolution** (the path callers compute):
    ```python
    basedir = Path(os.environ["MEMORY_STORE"]).resolve().parent
    audit_path = basedir / "dream" / f"{session_id}.redact-audit.jsonl"
    ```
    Equivalent to the shell expression `${MEMORY_STORE%/*}/dream/...`
    when `MEMORY_STORE` points to a file (the v1 assumption per
    [`ADR-harness-004`](ADR-harness-004-dream-state-sidecar.md)).
  - The exact env-var resolution rules (file vs directory, trailing
    slash, etc.) are halliday Finding #9 — queued for a successor ADR
    in the dreaming domain; until then, PR1 ships path *composition*
    only with `basedir` supplied by the caller, per rubric criterion
    104.
- **Policy — gitignore:** `*.redact-audit.jsonl` pattern added to the
  repo's `.gitignore` alongside `*.daydream-events.jsonl`. Verified by
  `test_gitignore_contains_redact_audit_pattern` and
  `test_gitignore_pattern_actually_ignores_audit_at_root` (the actual
  `git check-ignore` invocation) in
  `eval/memeval/dreaming/tests/test_redaction_gitignore.py`.
- **Policy — local-only invariant:** the audit file is never read by
  any LLM call, never transmitted, never logged remotely. Verified by
  two regression tests in
  `eval/memeval/dreaming/tests/test_redaction_audit.py`:
  - `test_audit_writer_makes_no_network_connect` — monkeypatches
    `socket.socket.connect` to raise; asserts a write completes
    without triggering any network connect.
  - `test_audit_writer_writes_only_to_supplied_path` — monkeypatches
    `builtins.open` to record write-mode opens; asserts the writer
    opens only the caller-supplied target path for write/append.
- **Policy — retention:** same TTL as
  [`ADR-dreaming-009`](ADR-dreaming-009-events-shim.md)'s events diary
  (TBD).
- **Exhaustive consumers** of the audit file: Daydream (writer), eval
  driver (reader, when implemented). Nothing else.

## Open items (dreaming-owned + cross-domain)
- **Audit file retention TTL** — paired with halliday's finding #8 on
  the events diary; one retention policy covers both.
- **Eval driver FP/FN computation** — Ken (eval-domain). Cross-domain
  hand-off; tracked separately from this ADR.
- **Periodic out-of-scope review** — every N weeks, scan audit file for
  novel patterns; if a pattern repeats, file a successor ADR adding a
  detector.
