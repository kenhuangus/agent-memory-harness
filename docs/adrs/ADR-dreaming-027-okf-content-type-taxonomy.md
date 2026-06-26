# ADR-dreaming-027 — Closed `okf_type` content-type taxonomy emitted by the daydream extractor

- **Status:** Proposed
- **Date:** 2026-06-26
- **Owner:** Scott (dreaming)
- **Contract:** true (a closed taxonomy that the daydream extractor produces and the OKF serializer / future on-disk grouping consumes; cross-workstream sign-off required to add/rename/remove a value)
- **Supersedes:** none. Extends the OKF serializer behavior established by [PR #171](https://github.com/kenhuangus/agent-memory-harness/pull/171) (`feat(okf): content-typed frontmatter — type≠provenance, real description, no redundant x_meta`).

## Context

The OKF Markdown frontmatter spec ([`docs/okf/integration-poc.md`](../okf/integration-poc.md)) requires exactly one field: a non-empty `type`, defined as

> **One required field:** `type` (a free string, e.g. `BigQuery Table`, `Playbook`). Consumers must tolerate unknown types.

`type` is the **content type** — what KIND of knowledge artifact this is — not where it came from.

**Status before PR #171**: the OKF serializer in `eval/memeval/okf.py` was emitting `type: daydream` (or `type: cookbook-memory`) — that's *provenance*, not a content type. Memories were OKF-shaped but non-portable: a memory dropped into another OKF knowledge base shouldn't carry our pipeline's plumbing label.

**Status after PR #171**: Brent fixed the leak at the serializer (`okf.py:289`):

```python
"type": meta.pop("okf_type", None) or "Memory",  # CONTENT type; provenance -> x_source
```

Every memory now writes `type: Memory` by default; provenance moved into a custom `x_source` key. **OKF-conformant in shape**, but the `type` value is the uninformative fallback on every single memory written today.

PR #171's body explicitly named the remaining work:

> The **rich** content (human `title`, a content-type vocabulary, an LLM-written `description`) is generated **upstream — primarily by the daydream extractor (cc @NerdAlert58)**: extend its extraction prompt + parse to emit `okf_title`/`okf_type`/`okf_description` (and `redact()` the description, per ADR-005). `okf.py` already plumbs those overrides through, so **no further serializer change is needed** — this PR's fallbacks are just the honest default until then. The content-type **taxonomy** is the shared input that work needs.

So three pieces are missing:

1. **A taxonomy** — the closed set of values `okf_type` can take. Without one, the LLM would invent strings ad-hoc, and the downstream on-disk content-typed grouping (PR #171's "separate, migration-bearing change") would be embarrassingly fragmented.
2. **The extraction prompt** doesn't tell the LLM to pick a content type.
3. **`_build_memory_item`** (in `eval/memeval/dreaming/_extract.py`) doesn't parse `type` from the LLM response — it only reads `{content, tags, relevancy}` today (the V2/V4 prompts already note this PARSER LIMITATION for `keywords`/`context` too).

This ADR records the taxonomy decision. The prompt-and-parser changes are downstream work that builds against it (separate PRs).

The V4 extraction prompt ([ADR-dreaming-N/A — landed in PR #185, `eval/memeval/dreaming/prompts.py` `EXTRACTION_SYSTEM_PROMPT_V4`](../../eval/memeval/dreaming/prompts.py)) already enumerates the right content categories in its INCLUDE list — they were tuned against the triage of 1,240 `daydream.candidate_rejected` events. Promoting that list to a closed taxonomy is the cheap path: same categories, formalized as a closed-set vocabulary the LLM picks from.

## Decision

Define a **closed eight-value taxonomy** for `okf_type`, plus `Memory` reserved as the off-list / fallback value (not selectable by the LLM):

| `okf_type` value | What it describes | Maps to V4 INCLUDE category |
|---|---|---|
| `Fix` | A fix recipe — symptom → solution, with enough detail to reapply | "the FIX is a separate durable fact; both can be kept" |
| `Bug` | A bug behavior — symptom + the conditions that trigger it (separate from the fix) | "bug behaviors — the SYMPTOM and the CONDITIONS that trigger it" |
| `Convention` | A codebase fact that won't change without an explicit change: class role, override boundary, custom manager's behavior, config contract, directory convention | "facts about the codebase that won't change without an explicit change" |
| `Invariant` | A language, framework, library, or protocol invariant — applies across tasks and repos | "language, framework, library, or protocol invariants" |
| `Workaround` | An established workaround, known pitfall, anti-pattern, correctness or security gotcha | "established workarounds, known pitfalls, anti-patterns, and correctness or security gotchas" |
| `Decision` | A decision with rationale — user, issue, doc, or agent-investigation; the *why* is durable | "decisions with rationale" |
| `Preference` | A recurring engineering preference, durable project convention, ongoing commitment | "recurring engineering preferences, durable project conventions, ongoing commitments" |
| `Identity` | Durable identity / role information about the user, agent, or project | "identity and preferences" |
| `Memory` *(reserved, not LLM-selectable)* | Fallback for off-list LLM output, for un-extracted memories from non-daydream paths (`memory_remember`), and for backward-compat with memories written before this ADR landed | n/a |

**Closed set, validator-enforced.** The downstream parser (`_build_memory_item` in `eval/memeval/dreaming/_extract.py`) MUST validate the LLM's emitted `type` against this set and fall back to `Memory` on any mismatch, emitting a `daydream.unknown_okf_type` event with the offending string so we can measure off-list drift in real bench runs.

**Source of truth.** The Python representation lives next to the prompts in `eval/memeval/dreaming/prompts.py` as a `frozenset[str]` constant (the variant that emits these values references the constant by import). This ADR is the spec; the code is the implementation; they MUST agree by code review.

**The values are content-typed, NOT provenance-typed.** A `Fix` written by daydream and a `Fix` written by an imported OKF bundle both file under `type: Fix`. Provenance stays in `x_source`. This is what makes the values portable into other OKF knowledge bases.

**OKF-conformant.** Each value is a non-empty title-case string — the spec's only hard constraint (`docs/okf/integration-poc.md:26-27` — "every non-reserved `.md` has parseable frontmatter with a non-empty `type`"). Consumers MUST tolerate unknown types per the spec, so an OKF KB importing one of our memories with `type: Workaround` Just Works whether or not the consumer has a `Workaround` template.

## Rationale

- **Why a closed set instead of free-string?** OKF allows free strings, but the dreaming-internal use case has two constraints that argue for closure: (a) the LLM will fragment a free-string field across synonyms (`Patch` / `Fix` / `Repair` / …) which makes downstream grouping noisy; and (b) the queued on-disk content-typed migration (PR #171 §Scope note) will branch on this value to organize files, and an open vocabulary creates an unbounded directory layout. The cost of closure is the LLM being unable to express truly novel content shapes — and we deliberately accept that, because the eight values already cover every false-negative class surfaced in the V4 triage (PR #185 §"Triage of 1,240 rejected").
- **Why these eight specifically?** They're not invented for this ADR — they're the V4 INCLUDE list, formalized. V4 was tuned against real reject rationales (django/haiku and sympy/sonnet runs); the categories survived that triage. Adding a ninth would be premature; removing one would re-open false-negative classes the V4 work closed. The mapping in the table is intentionally one-to-one so the ADR can be defended against either the prompt or the bench data.
- **Why `Memory` as fallback (not error)?** PR #171's serializer already defaults to `Memory`. Keeping the fallback path preserves backward-compatibility with memories written before this ADR lands AND with the `memory_remember` write path (which has no LLM and so cannot pick a content type). Erroring on unknown values would crash the daydream pipeline on a single ill-formed LLM response — directly contradicts the fail-open contract ([ADR-harness-006](ADR-harness-006-fail-open.md)).
- **Why a `daydream.unknown_okf_type` event?** Operational visibility. If the LLM goes off-list at material rates (say >5% of memories), we want to know whether to extend the taxonomy or tune the prompt's wording. Without the event, off-list landings are invisible; the silent fallback to `Memory` reads identically to a memory the LLM never categorized.
- **Why does this ADR carry `contract: true`?** The taxonomy is the *shared vocabulary* between the daydream extractor (producer), the OKF serializer (immediate consumer via `metadata["okf_type"]`), and the future on-disk content-typed grouping (downstream consumer per PR #171 §Scope note). Adding, renaming, or removing a value affects all three, so the closed set is governed cross-workstream and changes require sign-off from all of harness / storage / dreaming / eval owners — same gate as `schema.py`/`protocols.py`.

## Tradeoffs and risks

- **The LLM may go off-list more than expected.** The taxonomy was tuned against the V4 reject triage (django + sympy), and at least one general-purpose category (e.g. "Observation") didn't make the cut deliberately. Cold-data tasks may surface content shapes that fit none of the eight values cleanly. **Mitigation:** the `daydream.unknown_okf_type` event captures the LLM's chosen string verbatim so we can measure exactly how often this happens. A taxonomy-extension ADR can promote a frequently-cited unknown string to a ninth value once we have data.
- **Closure is opinionated, and OKF says it shouldn't have to be.** OKF tolerates unknown types as a spec-level guarantee. Closing the set for our own use is a self-imposed discipline, not an OKF requirement. **Mitigation:** the ADR documents that we're choosing closure for *internal grouping reasons*, not because OKF demands it — so a future fork that wants a richer set can subset/superset our taxonomy without violating OKF.
- **The taxonomy ships separately from the prompt+parser changes that consume it.** This ADR landing doesn't change one bit of memory data; downstream PRs do. Risk: the ADR ships, no prompt change follows, and the closed set sits unused. **Mitigation:** the V5 prompt + `_build_memory_item` plumbing land as a single PR titled e.g. `feat(dreaming): V5 emits OKF content-type per memory + plumb okf_type through extractor`, gated on this ADR being Accepted. The split keeps the *vocabulary* decision separate from the *prompt* engineering, but both should be reviewable together when work begins.
- **Backfilling existing memories is out of scope.** Every memory written before V5 ships will carry `type: Memory` (the serializer fallback). Retroactive content-typing would require either re-running the extractor or a heuristic backfill from `tags` — both have meaningful cost and uncertain value. **Decision:** leave them. Going forward, new memories are properly typed; old memories remain queryable via the existing `tags` + content surface.
- **The taxonomy presupposes a software-engineering-ish use case.** `Fix` / `Bug` / `Invariant` / `Workaround` are coding-oriented. A memory store used in a non-SWE domain (legal, research, product strategy) would find this vocabulary narrow. **Mitigation:** acknowledged. If/when the cookbook-memory plugin is used outside SWE workflows, a new ADR (likely a sibling, not a supersession) would define a parallel taxonomy for that domain. The closed-set design accommodates that — adding a ninth value or replacing the set wholesale is a clean ADR change rather than an architectural one.
- **`x_source` and `type` together duplicate information for daydream's typical case.** A daydream-extracted `Fix` will have `x_source: daydream` and `type: Fix`. Two facts about the same memory. **Accepted.** The duplication is *exactly* the separation Brent's PR #171 introduced: source ≠ content type, deliberately. Recombining them would re-introduce the original bug.

## Consequences

- **Immediate (this ADR landing):** zero code change. The taxonomy is a spec; no behavior shifts until the V5 prompt + parser PR ships.
- **Downstream PR #1 — V5 prompt + parser:** introduces `EXTRACTION_SYSTEM_PROMPT_V5` (registered identically to V4 per [ADR-dreaming-023](ADR-dreaming-023-selectable-extraction-prompt-variants.md) — V4 stays untouched so its `b2f8f69bcff4` SHA stays stable for forensics). V5's schema adds a required `type` field with an enum of the eight values. `_build_memory_item` validates and falls back to `Memory` on unknown, emitting `daydream.unknown_okf_type`. New variant means the substrate keep-rate sweep does NOT need to re-run (V5 is additive metadata, not a threshold change).
- **Downstream PR #2 — doc fix:** correct the stale row in `docs/okf/integration-poc.md:37` that maps `source → type` (now wrong post-PR #171). Brent flagged this in #171.
- **Downstream PR #3 — V5 promotion to default:** gated on A/B measurement on the bench. V0 stays the default extraction variant until V5's content-typing is shown to be a net win (specifically: no regression in V4's reject precision; off-list rate from `daydream.unknown_okf_type` events under 5%).
- **Downstream PR #N — on-disk content-typed migration:** PR #171's flagged "separate, migration-bearing change." Moves `<basedir>/markdown/<source>/<id>.md` → `<basedir>/markdown/<okf_type>/<id>.md` and walks existing stores to re-file. Cross-coordinates with the graph store (uses the on-disk path in its anchor tuple per [ADR-storage-006](ADR-storage-006-typed-directional-graph-edges-okf-anchor-tuple.md)). This is the heaviest downstream piece and gets its own ADR (likely under `storage`, since it's a layout/migration decision the storage owner gates).
- **For other workstreams:**
  - **eval** — none. The OKF serializer's frontmatter shape is unchanged (still `type: <string>`); only the value distribution shifts. Existing eval tests that check for `type: Memory` will need to relax to `type` ∈ taxonomy (a one-line test update per assertion).
  - **storage** — none immediately. When PR #N (on-disk migration) lands, the storage workstream owns the layout decision; this ADR is the *vocabulary* input that migration ADR will reference.
  - **harness** — none. The plugin's `memory_remember` MCP path doesn't go through the daydream extractor; its memories will continue to carry `type: Memory` (the serializer fallback) unless the caller supplies `metadata["okf_type"]` explicitly. That's a follow-up worth surfacing to Keith but not blocking on.
- **Observability:** add `daydream.unknown_okf_type` to the dreaming-domain event vocabulary (currently 9 names after [ADR-dreaming-026](ADR-dreaming-026-noise-filter-pre-pass.md)'s `daydream.noise_filtered`). The AST-pinned allow-set test in `test_extract.py` (per [ADR-dreaming-023](ADR-dreaming-023-selectable-extraction-prompt-variants.md) §Open items) will need the new name added — covered by the V5 prompt PR.

## Open items

- **Whether `okf_title` and `okf_description` taxonomies need ADRs too.** PR #171 also named those as upstream work for me. `okf_title` is a free string (per-memory human label) and `okf_description` is a free-text first-sentence summary — neither is a closed set, so they probably don't need their own ADR. The V5 prompt PR can add them as additional schema fields alongside `type` without a separate decision record. To confirm with Brent.
- **The `memory_remember` write path.** Memories written via the agent's direct MCP call have no LLM so cannot pick a content type. Today they default to `type: Memory`. Worth a small follow-up: could the agent's tool surface accept an optional `okf_type` parameter from the calling agent? Not blocking; can be a separate ADR or just a tool-surface enhancement.
- **When does the substrate keep-rate sweep need to re-run?** Per the V5 PR description: never — V5 is purely additive metadata. But: if V5's prompt changes how aggressively the LLM kept content (because adding the type-selection task changes the prompt's framing), the keep rate could shift as a side effect. Worth a small empirical check on landing — `daydream-replay` on the existing fixture slice with V5 should produce the same kept count as V4 to within run-to-run variance, otherwise the V5 prompt needs tuning.
