---
id: ADR-dreaming-005
domain: dreaming
title: v1 Daydream inlines log reading + secret redaction (Claude-only, detect-secrets structured detectors + custom plugins)
status: Accepted
date: 2026-06-20
contract: true
supersedes: none
superseded_by: none
owner: Scott B. (P4) — engine; Keith (P1) — informed (long-term adapter owner)
origin: design session 2026-06-20 (Daydream PR1 planning), validated by 2026-06-20 spike
---

# ADR-dreaming-005: v1 Daydream inlines log reading + secret redaction (Claude-only, `detect-secrets` structured detectors + custom plugins)

**Status:** Accepted · **Date:** 2026-06-20 · **Contract:** yes
**Supersedes:** none · **Superseded by:** none

> **Scope.** This ADR records a deliberate v1 deviation from
> [`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md) for the Daydream
> path only. The broader harness adapter decision stands for other harnesses
> and the eventual multi-harness Claude implementation. When that adapter
> ships, a successor ADR records the migration and this ADR moves to
> `Superseded`.

## Context
[`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md) (Keith's domain)
commits to a "log adapter" that normalizes JSONL and redacts secrets before
any model call. That adapter is unbuilt. Daydream's PR1 needs to read JSONL
and call an LLM today; sequencing PR1 behind Keith's adapter would slip
Scott's work indefinitely. The targeted v1 surface is Claude Code only — the
multi-harness normalization doesn't yet earn its keep.

A 2026-06-20 spike against
[`detect-secrets`](https://github.com/Yelp/detect-secrets) v1.5.0 produced
four load-bearing findings:

1. The **structured pattern-based detectors** (AWS, GitHub, JWT, OpenAI,
   PrivateKey, etc.) work cleanly via `plugin.analyze_line(filename, line,
   line_number)` — near-zero false positives on prose.
2. The **entropy-based detectors** (`Base64HighEntropyString`,
   `HexHighEntropyString`) are unusable on session logs at any threshold —
   they flag every English word ≥4 chars as a "secret." Calibration targets
   source code, not prose.
3. The documented **`transient_settings` + path-based custom plugin loading**
   is non-functional in v1.5.0 for in-process Python plugin classes. Driving
   plugins directly via `plugin.analyze_line()` works and is simpler anyway.
4. The catalog leaves three gaps relevant to this project: no Anthropic key
   detector, no OpenRouter key detector, no Google Cloud key detector.

## Options considered
- **Inline reader + curated structured-detector list + four custom plugins,
  driven directly via `analyze_line()`** (chosen) — uses detect-secrets for
  the catalog of structured patterns it does well, ignores its entropy
  detectors which the spike proved broken for prose, closes the catalog gaps
  with local custom plugins.
- Inline reader + full default detect-secrets scan — rejected: spike proved
  the entropy detectors destroy prose by redacting common words.
- Inline reader + tuned entropy thresholds (4.5 → 4.0) — rejected: spike
  proved lowering thresholds makes prose noise strictly worse.
- Inline reader + hand-rolled regex (no library) — rejected: re-implements 23
  curated patterns we'd write worse than Yelp does.
- Block PR1 until Keith's adapter ships — rejected: sequential cross-domain
  dependency the three-iteration ramp avoids.

## Decision
**v1 Daydream reads the Claude Code session JSONL directly AND performs
secret redaction inline before any content reaches `LLMClient.complete()`.**
Redaction:

1. **Drives a curated list of structured detector plugins directly** via
   `plugin.analyze_line(filename, line, line_number)`. No `scan_line()`, no
   `transient_settings`. Plugin instances live for the life of the redaction
   function. v1 plugin set (subject to add/remove as patterns emerge):
   `AWSKeyDetector`, `AzureStorageKeyDetector`, `GitHubTokenDetector`,
   `GitLabTokenDetector`, `SlackDetector`, `StripeDetector`, `OpenAIDetector`,
   `JwtTokenDetector`, `PrivateKeyDetector`, `BasicAuthDetector`,
   `ArtifactoryDetector`, plus the four custom plugins below.
2. **Excludes the entropy detectors** (`Base64HighEntropyString`,
   `HexHighEntropyString`) from v1. Their calibration is unfit for prose; a
   scoped entropy check (e.g. only inside ``` fences) is a follow-up if eval
   shows novel-token leaks.
3. **Adds four Daydream-local custom plugins** to close catalog gaps:
   - `AnthropicKeyDetector` — `sk-ant-api03-...`, `sk-ant-sid01-...`
   - `OpenRouterKeyDetector` — `sk-or-v1-...`
   - `GoogleCloudKeyDetector` — `AIza[0-9A-Za-z\-_]{35}`
   - `BearerTokenDetector` — `Authorization: Bearer <token>` headers

Detector verdicts (plugin name + redaction count per chunk) emit through the
memory-events stream
([`ADR-harness-007`](ADR-harness-007-memory-events-stream.md)).

When the multi-harness log adapter from
[`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md) lands, Daydream
switches to calling it; a successor ADR records the migration.

## Rationale
Spike-validated. Driving plugins directly is the smallest seam: import the
classes we want, instantiate, call `analyze_line()` per line. No settings
context manager, no YAML config, no path-based dynamic loading. The
structured detectors are the curated regex catalog we'd otherwise re-implement
badly; the entropy detectors are a poor fit for prose at any threshold so
they don't enter the pipeline at all. The four custom plugins close the
gaps that matter most for a Claude-focused, OpenRouter-routed project. The
collapse is reversible: when Keith's adapter ships, the call sites collapse
into one adapter call.

## Tradeoffs & risks
- **No entropy-based catch-all in v1.** A novel obfuscated secret (a token
  format we have no pattern for) reaches OpenRouter. Acceptable per the
  "best-effort, accept residual leak risk" posture chosen in the same design
  session. Reported through the events stream so coverage gaps are visible
  in eval data.
- **Cross-domain temporary ownership.** Daydream holds a concern Keith's
  harness owns long-term. Risk: migration slips, inline implementation
  calcifies. Mitigated by tracking the migration and writing this ADR as an
  explicit deviation (calcification visible in the ADR index).
- **`transient_settings` path-loading is broken in detect-secrets v1.5.0.**
  Not a blocker (we drive plugins directly), but worth tracking — upstream
  bug report + potential PR after PR1.
- **Custom plugins are this repo's responsibility until upstreamed.** Live
  in `eval/memeval/dreaming/redaction/plugins/`. An upstream change to
  `RegexBasedDetector` could break them — pin `detect-secrets` version in
  `eval/pyproject.toml`.
- **Dependency footprint.** Adds `detect-secrets` to the dreaming module
  (lazy-imported per `architecture.md` §3). Transitive: `requests` is eager
  at module top — confirmed by spike — which makes the lazy import
  non-optional.
- **Two paths during migration window.** When Keith's adapter exists but
  before Daydream cuts over, both readers exist. Mitigated by keeping the
  cutover small (one call-site change) and writing the successor ADR at the
  same time.

## Consequences for the build
- **Contract — source of truth:** the redaction function inside
  `eval/memeval/dreaming/redaction/`.
- **Shape:** `redact(text: str) -> str`. For each line, drives every plugin
  in a curated module-level list via `analyze_line(filename="<daydream>",
  line=line, line_number=lineno)`; replaces each detected `secret_value` span
  in the line with `[REDACTED:<secret_type>]`; returns the cleaned string.
  Lazy-imports `detect_secrets` and the plugin classes inside the function
  body.
- **Policy — every string Daydream passes to `LLMClient.complete()`** (system
  prompt + user prompt + any in-context log content) routes through
  `redact()` first. No exceptions — including tests against `EchoClient`,
  so test surface exercises the production path.
- **Policy — no entropy detectors in v1.** Adding one requires a successor
  ADR after eval data shows it's needed and a scoping mechanism (e.g.
  code-fence-only) is in place.
- **Policy — no `transient_settings`, no `scan_line()`, no YAML config.**
  Direct `plugin.analyze_line()` is the only path.
- **Policy — no network verification.** detect-secrets' `--only-verified`
  behaviors (which call provider APIs to confirm validity) are unreachable
  in this code path. A regression test asserts no network connect happens
  during a scan.
- **Policy — custom plugins:** under
  `eval/memeval/dreaming/redaction/plugins/`, each inheriting from
  `detect_secrets.plugins.base.RegexBasedDetector`. Contribute upstream per
  plugin after v1 settles.
- **Policy — events stream:** redaction events emit through
  [`ADR-harness-007`](ADR-harness-007-memory-events-stream.md) with shape
  `{plugin: <secret_type>, count: <n>}` per chunk.
- **Policy — dependency pin:** `detect-secrets` pinned in
  `eval/pyproject.toml` to the spike-validated version (v1.5.0); upgrades
  follow a checklist (regression test + custom plugin compatibility + entropy
  detector behavior unchanged).
- **Policy — migration path:** when
  [`ADR-harness-005`](ADR-harness-005-log-adapter-redaction.md)'s adapter
  lands, Daydream's reader + `redact()` calls collapse into one adapter
  call. A successor ADR (Scott authors, Keith co-signs) records the
  migration.
- **Exhaustive consumers:** Daydream chunk-extraction loop in
  `eval/memeval/dreaming/`. Dreaming (night, per
  [`ADR-dreaming-002`](ADR-dreaming-002-dreaming-consolidation-cli.md)) reads
  the store, not JSONL — does not call `redact()`.

## Open items (dreaming-owned)
- **Scoped entropy detection** as a follow-up if eval shows novel-token
  leaks: e.g. run entropy detectors only inside fenced code blocks
  (where the prose-noise concern doesn't apply).
- **Upstream contribution:** PR each custom plugin to `Yelp/detect-secrets`
  after v1 settles (one PR per plugin to maximize merge odds).
- **Upstream bug report:** investigate `transient_settings` + path-loading
  failure for in-process plugin classes; PR a fix if a clean diff exists.
- **False-positive rate measurement:** instrument PR1 testing to count
  `[REDACTED:*]` spans per chunk. If FP rate destroys extraction quality,
  revisit the curated plugin list.
- **Entropy detectors via `analyze_line()` may be safe to include after all.**
  Post-spike regression testing (2026-06-20) found that
  `Base64HighEntropyString.analyze_line()` returns zero findings on prose
  that `scan.scan_line()` + `default_settings()` floods with false positives.
  The "broken on prose" property may be specific to the `scan_line` + settings
  chain — which this ADR forbids — rather than inherent to the detector
  class. Tracked by
  `test_entropy_detector_via_analyze_line_silent_on_prose`. If that test
  remains green across several `detect-secrets` upgrades, the v1 exclusion
  policy could be revisited in a successor ADR; until then, "exclude as
  conservative default" stands.
