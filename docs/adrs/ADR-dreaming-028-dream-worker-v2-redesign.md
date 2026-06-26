# ADR-dreaming-028 — Dream worker v2: type-conditioned retention + streaming neighborhood-scoped consolidation + deduction/induction specialist split

- **Status:** Proposed
- **Date:** 2026-06-26
- **Owner:** Scott (dreaming)
- **Contract:** true (replaces the single-pass `worker.dream` contract established by [ADR-002](ADR-dreaming-002-dreaming-consolidation-cli.md); changes what consumers of the post-dream store see; cross-workstream sign-off required because storage and eval observe the consolidation output)
- **Supersedes:** does not supersede prior ADRs; extends the dream-side decisions in [ADR-002](ADR-dreaming-002-dreaming-consolidation-cli.md) (whole-store consolidation entrypoint) and [ADR-021](ADR-dreaming-021-dream-mutation-concurrency.md) (mutation under basedir flock). Both remain in force — the entrypoint and the lock discipline don't change; only what runs *inside* the lock does.

## Context

Today's dream worker (`eval/memeval/dreaming/worker.py:929+`) executes three sequential passes against the whole store under a basedir flock:

1. **TTL pruning** — items older than `DREAM_ITEM_RETENTION_DAYS` (flat 30-day default) are hard-deleted, regardless of content.
2. **Lexical dedup** — content normalized (lowercase, strip punct, collapse whitespace), exact-key clusters retire all but the latest-timestamp winner.
3. **LLM contradiction resolution** — post-dedup survivors batched and judged for flat disagreement; loser hard-deleted per pair.

All three passes operate on a single `items: list[MemoryItem] = list(self.store.all())` snapshot loaded at worker entry (`worker.py:984`).

This shipped well at bench scale (hundreds of memories per store). At the scale long-running agents accumulate — Letta-paper territory at 10K-100K memories per agent over months — and against the content-typing now landing per [ADR-027](ADR-dreaming-027-okf-content-type-taxonomy.md), four structural problems are visible:

1. **Flat-age TTL is wrong for typed content.** ADR-027's taxonomy explicitly categorizes some memory shapes (`Identity`, `Convention`, `Invariant`, `Workaround`) as durable by design — facts that don't become less true with calendar time. Hard-deleting a six-month-old Python protocol `Invariant` at the 30-day mark destroys exactly the content the system was built to retain. Confirmed against three community projects ([Honcho](https://honcho.dev), [Letta](https://letta.com), [Mem0](https://mem0.ai)) — none of them age-bomb durable content on a calendar; our flat default is an outlier, not a community pattern.
2. **`list(self.store.all())` doesn't scale.** At ~200-byte items × 100K memories the in-memory load is ~20 MB — tractable but tight. At 1M it's not. Dream cannot run at multi-week real-use scale on the current read pattern, regardless of which downstream passes execute. The available backends Brent shipped ([ADR-storage-007](ADR-storage-007-neo4j-bolt-phase-a-parity-floor.md) and [PR #174](https://github.com/kenhuangus/agent-memory-harness/pull/174) FTS5, [PR #151](https://github.com/kenhuangus/agent-memory-harness/pull/151) sqlite-vec ANN) expose streaming and search primitives the worker bypasses.
3. **The contradiction pass conflates updates with disagreements.** Two memories that say "the user prefers tabs" and "the user prefers spaces" carry different semantic weight depending on which was asserted first and whether the later one was a *change of mind* (update) or a *flat disagreement* (contradiction). Today's pass silently picks a winner in both cases and deletes the loser. Honcho's specialist drew the distinction we collapsed (verified in `agent_tools.py:447`): updates delete the loser, true contradictions emit a new contradiction-level observation that preserves both originals as data.
4. **Synthesis is absent.** The extractor (`_extract.py`) and the dream worker both work on individual memories. Nothing in the system reads across multiple `Fix` memories about similar bugs and promotes the pattern to a durable `Invariant`. Honcho's induction specialist (creation-only authority, can synthesize across a peer-card slice) and Mem0's NOOP-aware judgment surface that capability; we don't have a comparable mechanism. Confirmed: induction-style synthesis isn't anywhere in our code today.

The four problems are entangled — the streaming read (#2) enables neighborhood-scoped LLM work which enables the specialist split (#4) which is what makes the contradiction-as-data shape (#3) feasible, all of which only makes sense if retention (#1) respects content type.

## Decision

The four-part Dream worker v2 redesign. Each part is a separate implementation PR (sequenced in §Consequences), but the decision is one architecture because the parts cannot be separated without losing coherence.

### 1. Type-conditioned retention

Replace `DREAM_ITEM_RETENTION_DAYS` with a per-type table read from a module-level `TYPE_RETENTION_DAYS: dict[str, int | None]` constant. Source-of-truth in `worker.py`, alongside the existing `_DEFAULT_ITEM_RETENTION_DAYS = 30`. `None` means *no calendar TTL* — the item stays until a non-age signal (contradiction, dedup, code-change detection — separate future ADR) removes it.

| `okf_type` | Retention | Why |
|---|---:|---|
| `Identity` | none | Names, roles, durable preferences — don't decay by time |
| `Convention` | none | Stays true until code changes — code-change is the signal, not the calendar |
| `Invariant` | none | Language / framework / library facts — stay until version bumps |
| `Workaround` | none | Stays relevant until the underlying bug is fixed |
| `Bug` | none | Same logic as Workaround — bug-still-broken is the signal, not age |
| `Contradiction` (new, worker-emitted — see §4 and the ADR-027 amendment below) | none | A recorded disagreement stays as data until explicitly resolved; calendar is the wrong signal |
| `Decision` | 365 days | Decisions retain value as historical record but eventually lose specificity to current state — annual horizon balances "decisions stay queryable" with "year-stale rationale rarely informs current choices" |
| `Fix` | 90 days | Specific recipes generalize as `Invariant`/`Workaround` lessons (via the induction pass, §3); the raw `Fix` itself goes stale faster as code evolves — quarterly horizon matches typical refactor cadence |
| `Preference` | 180 days | The one type where age is a real, if weak, signal — users drift on a quarter-ish horizon |
| `Memory` (fallback for off-list / pre-V5) | 30 days | Back-compat with current behavior; matches today's `_DEFAULT_ITEM_RETENTION_DAYS` |

`DREAM_ITEM_RETENTION_DAYS` is **kill-switch-only** in v2 — `0` disables ALL TTL regardless of type; any other value is ignored. The per-type values above are code-level constants in `TYPE_RETENTION_DAYS` (`worker.py`), not env-tunable. Reasoning: the only operational use case for changing retention at runtime is the kill-switch ("retain everything during this run"); per-type adjustment needs cross-workstream review and lives at the code level. Operators who want different per-type retention edit the constant and ship a PR.

### 2. Streaming neighborhood-scoped consolidation

Replace the single `list(self.store.all())` load with a streaming iterator (`store.iter_pages(page_size=N)` — new method on the `MemoryStore` protocol, default implementation falls back to today's behavior so backends not yet supporting streaming keep working). The worker walks pages, holding only one page in memory at a time.

For each item in the page, the worker calls `store.search(item.content, k=K)` (default K=10) to get the nearest neighbors. That 11-item stack — the item plus its K closest matches — is the consolidation working set for that item. The whole store is never materialized.

Backend cooperation: streaming requires either FTS5 or vector backend wired. Worker detects backend capability at start and falls open to the legacy single-load path when no streaming-capable backend is available. Operators wiring those backends for recall (PR #151, PR #174) get dream-side benefits automatically.

### 3. Deduction / induction specialist split

The single `worker.dream` becomes two sequential workers under the same basedir flock:

- **Deduction (cleaner)** — Authority: DELETE. For each page-streamed item, takes the 11-item neighborhood stack, executes lexical dedup, runs the LLM judgment for the 4 NOOP-aware operations Mem0 uses: `NOOP` (nothing to do), `UPDATE` (loser is a stale version of winner — delete loser), `DELETE` (exact duplicate after normalization), `CONTRADICTION` (see §4 — preserves both, emits new card). Type-conditioned retention runs first within the deduction pass (same in-flock guarantees as today, per ADR-021).
- **Induction (generalizer)** — Authority: CREATE only. After deduction completes, reads a recent slice of post-deduction survivors looking for clusters of related cards (e.g., three or more `Fix` cards for structurally similar bugs). Emits new synthesized cards typed as `Invariant` or `Convention`, with provenance metadata naming the source items. Cannot delete; cannot touch the deduction's working set. Capped by a per-run LLM call budget (separate from contradiction budget).

The split mirrors Honcho's deduction/induction architecture but stays inside our offline dream cycle — we are not introducing a new write-time path. The agent's hot path is unchanged.

### 4. Contradiction-as-data with provenance

When the deduction LLM returns `CONTRADICTION` (semantically true disagreement, not stale update), the worker does NOT pick a winner. Instead:

- Both original items remain in the store.
- A new card is created (alongside, not replacing) typed `okf_type: Contradiction` (the new ninth value — see the ADR-027 amendment below), carrying `metadata.contradiction_of: [item_id_A, item_id_B]` naming both originals; the content body summarizes what disagrees. This makes the disagreement a first-class queryable artifact with its own retention policy (`none` — stays as data until explicitly resolved), instead of silently destroying it.

### 5. Extension to ADR-027: add `Contradiction` as a worker-reserved ninth taxonomy value

The closed taxonomy in [ADR-027 §Decision](ADR-027-okf-content-type-taxonomy.md) grows from eight LLM-selectable values + `Memory` (parser-reserved) to **eight LLM-selectable values + `Memory` (parser-reserved) + `Contradiction` (worker-reserved)**. The amendment:

- `Contradiction` is added to the `OKF_CONTENT_TYPES` constant in `eval/memeval/dreaming/prompts.py` so `_build_memory_item` accepts it without firing `daydream.unknown_okf_type`.
- `Contradiction` is NOT added to the V5 prompt's enumerated taxonomy list — the daydream extractor operates on a single session transcript and cannot detect cross-memory disagreement at extract time, so the LLM has no business emitting this value.
- Like `Memory`, `Contradiction` is reserved: not in the V5 prompt's selectable list; produced only by a specific code path (`Memory` by the parser fallback, `Contradiction` by the dream worker's deduction pass).

This is a contract change to ADR-027 (whose taxonomy is `Contract: true`) and requires the same cross-workstream sign-off the original ADR-027 review carries. The amendment is small in surface (one line added to a `frozenset`) but real in consequence — every consumer that reads `okf_type` needs to know `Contradiction` is in the legitimate set.

ADR-027 itself remains in force as the foundational taxonomy decision; this ADR extends it rather than supersedes it. (ADR-027 is still in `Proposed` status as of this draft, which would let it be edited in-place under the docs/adrs/README.md governance rules; the choice to extend via ADR-028 rather than edit ADR-027 keeps the original review intact and makes the worker-only reservation traceable in the historical record.)

## Rationale

- **Why type-conditioned retention specifically.** ADR-027 already encodes the durability characteristics in the taxonomy: `Identity`/`Convention`/`Invariant`/`Workaround` are categorized as durable BY DESIGN. Applying a flat 30-day TTL to those types directly contradicts the taxonomy's semantics. The honest move is to make retention follow the type the LLM already assigned, with `Preference` and `Memory` as the legitimate calendar-decay carve-outs. This is the conclusion the user surfaced ("why would we arbitrarily delete memories based on age?") and the only design that respects both the user's pushback and the taxonomy's framing.
- **Why streaming + neighborhood-scoped (Mem0-inspired) and NOT write-time (Mem0's actual design).** Mem0 puts dedup + contradiction on the write path; doorman model. We deliberately keep consolidation OFFLINE (per the user's correction earlier in design discussion — this is a load-bearing architectural choice for our system). The relevant insights from Mem0 are the *neighborhood-scoped LLM judgment* (k=10 nearest, not whole store) and the *NOOP-aware operation set* (NOOP/UPDATE/DELETE/CONTRADICTION). Both port cleanly into an offline cycle; the cost is amortized across cards-changed-since-last-cycle instead of total store size.
- **Why deduction/induction split (Honcho-inspired).** Today's single-pass worker conflates cleanup (delete authority) with synthesis (create authority) and gives both to one mechanism, which is why we don't have synthesis at all today — adding it to the existing pass would create the very conflation Honcho's architecture avoids. The split is what unlocks the `Invariant`/`Convention` promotion pipeline (induction synthesizes upward from clusters of lower-durability cards). Honcho's confirmed implementation pattern (deduction has delete authority over the peer-card; induction is creation-only and requires ≥2 source observations per emit) is the template; we adopt the authority split and the provenance-on-create requirement.
- **Why contradiction-as-data instead of silent loser-delete.** A flat-disagreement signal is operationally interesting — it's the kind of thing an operator wants to inspect in the inspector UI ("show me everywhere two memories actively disagree"). Today's pass destroys that signal in the act of resolving it. Honcho's approach (emit a new observation rather than pick a winner) preserves the disagreement as queryable data. The cost is a slightly larger store; the benefit is observability and the ability to layer a future "human-in-the-loop disagreement resolution" mechanism without re-architecting.
- **Why offline cycle stays offline.** The user explicitly corrected my first redesign attempt that moved dedup to write-time — the offline cycle is a deliberate architectural choice for keeping the agent's hot path fast and decoupled from consolidation logic. Every decision in this ADR respects that constraint: streaming reads run in the dream worker, not at insert; the deduction LLM judgment runs in the dream worker, not in the recall path; the induction synthesis is its own offline phase.
- **Why this is one ADR, not four.** The four sub-decisions are entangled. Streaming enables neighborhood-scoped LLM work which enables the specialist split which is what makes contradiction-as-data feasible — and all four only cohere if retention respects content type. Reviewing them as separate ADRs would force readers to re-derive the dependency from scratch each time. The implementation PRs that follow can land sequentially per §Consequences without sequencing problems; the *decision* is unified.

## Tradeoffs and risks

- **Per-cycle cost rises at small scale.** Today's worker makes roughly `N / batch_size` LLM calls per run (e.g., 10 calls for 100 memories at batch_size=10). The proposed worker makes one LLM judgment call per *card changed since last cycle*. At day-1 bench-arc scale this is more expensive per run — ~10× more LLM calls in the worst case. Crossover with today's model happens around 2-4 weeks of accumulated agent use (store grows linearly with time under today's design; proposed cost flat as long as new-cards-per-cycle is bounded). **Mitigation:** the design is most defensible against the multi-week real-use horizon, which is what we're trying to grow toward. For the bench arc, the gap is acceptable.
- **More tunable surface.** Today has one knob (`DREAM_ITEM_RETENTION_DAYS`). Proposed has: per-type retention table (code-level), `K` for ANN neighborhood size (env-tunable), similarity threshold for "near-duplicate" judgment (env-tunable), induction cadence + call budget (env-tunable). **Mitigation:** all knobs ship with defaults measured empirically against the first real bench run; documented in worker.py as a table.
- **Loss of full determinism in dedup.** Today's lexical normalize is provably reproducible. Proposed dedup uses ANN-scored neighborhoods + LLM judgment — embedding model, threshold, and LLM judgment all introduce variability. **Mitigation:** ANN is still seeded by a deterministic embedding model + a fixed threshold; LLM determinism via temperature=0 in the judgment client; tests pin behavior with fixture neighborhoods rather than against a live LLM (extends the V0-V4 pattern from ADR-023).
- **Backend dependency.** Streaming + ANN require FTS5 or vector backend wired. Stores built without them fall back to today's `.all()` path. **Mitigation:** explicit capability detection at worker start, logged loudly so operators see "running in legacy mode" rather than silent degradation. The fallback is the safe default.
- **Contradiction-as-data inflates store size.** Each unresolved contradiction adds a card. **Mitigation:** acceptable — disagreement-as-data is the value being purchased; if growth becomes a problem we can add a separate eviction signal for "contradiction observation that's been resolved/superseded." Future ADR territory.
- **Eval / test surface changes.** Today's dedup tests are easy to pin (`_normalize` produces stable keys). Proposed dedup needs fuzz tolerance or fixture-driven embedding seeds. The contradiction pass tests need an LLM mock that returns the 4-op vocabulary. **Mitigation:** tests are reauthored per-pass alongside the implementation PRs; existing test invariants (ADR-021 lock discipline, ADR-013 cursor-advance semantics) preserved.
- **Induction synthesis is the riskiest piece.** It writes new cards based on LLM inference across multiple originals. Hallucination risk is real. **Mitigation:** induction is create-only with mandatory provenance — every emitted card carries `metadata.synthesized_from: [item_id, item_id, ...]` listing the source observations. Operators can audit synthesized cards against their provenance. Conservative posture: induction ships with a much lower call budget than deduction initially, gated on real-bench measurement of synthesis quality.

## Consequences

### Implementation sequence

Five PRs landing in this order:

1. **PR #0 — `OKF_CONTENT_TYPES` taxonomy amendment.** One-line change adding `Contradiction` to the `frozenset` in `eval/memeval/dreaming/prompts.py` per §5 above. Updates the SHA-pinned test for the constant (if pinned) + the AST allow-set test in `test_extract.py` so `_build_memory_item` accepts `Contradiction` without firing `daydream.unknown_okf_type`. V5 prompt body unchanged — `Contradiction` is worker-reserved, never LLM-selectable. Smallest of the five; lands first because every subsequent PR's tests need `Contradiction` to be a recognized value.
2. **PR #1 — Type-conditioned retention.** `worker.py` gains the `TYPE_RETENTION_DAYS` table; `_pick_pruned` looks up per-item `metadata.okf_type` and uses the type-specific value. `DREAM_ITEM_RETENTION_DAYS=0` remains the kill-switch. Pre-V5 memories carrying `okf_type: Memory` (the parser fallback per ADR-027 §Decision) age out at 30 days, preserving back-compat. Smallest of the four implementation PRs after the amendment; lands first because it's independent and the easiest to roll back if behavior surprises.
3. **PR #2 — Streaming read + ANN-narrowed candidate set.** Extends the `MemoryStore` protocol with `iter_pages(page_size: int)` (default implementation = wrap `.all()`); FTS5 + sqlite_store backends get streaming implementations. Worker's main loop replaced with a page-walk that, for each item, does `store.search(item.content, k=K)` to fetch neighbors. The TTL pass from PR #1 still runs first; dedup logic from today still runs against the neighborhood stack. Contradiction pass moved to neighborhood-scoped (no more whole-store batch).
4. **PR #3 — Deduction/induction split.** Refactors `worker.py` so the single `dream()` becomes `_run_deduction()` + `_run_induction()` executing in sequence under the same basedir flock. Deduction inherits the work from PR #2 (page-walk + neighborhood). Induction is a new pass: reads a recent slice of survivors, clusters by type-and-content-similarity, emits new synthesized cards with provenance. CREATE-only authority enforced at the store-call level.
5. **PR #4 — Contradiction-as-data with provenance.** Replaces the silent loser-delete on `daydream.contradiction_pair` events with the new card creation flow. New event `dream.contradiction_observation_emitted` with the new card's id + both originals. JOB2_CONTRADICTION_RUBRIC.md updated to reflect the new shape.

Each PR has its own tests + AST allow-set updates + (where applicable) RUBRIC.md updates. Each is independently revertible.

### Cross-workstream impact

- **storage** — Brent's `MemoryStore` protocol gets `iter_pages` (PR #2). FTS5 backend (PR #174) and sqlite_store backend (#151) implement streaming. Markdown backend can fall back to wrapping `.all()`. Graph backend unaffected.
- **eval** — Bench summaries gain new fields for induction-synthesized card counts + contradiction observation counts. Existing summary shape preserved; additions are additive.
- **harness** — No changes. Agent's hot path unchanged; `recall` still goes through the router; `daydream` still fires on Stop hook.
- **observability** — Three new events: `dream.deduction_started`, `dream.induction_started`, `dream.contradiction_observation_emitted`. AST allow-set test in `test_extract.py` will need updating in PR #4 along with `worker.py`'s allow set.

### What this does NOT change

- The dream-cycle ENTRYPOINT (CLI invocation via `daydream-cli dream --all`) per ADR-002. No new triggers, no cron, no auto-fire.
- The basedir flock discipline (ADR-021). The whole v2 worker runs under the same single basedir flock.
- The fail-open contract (ADR-harness-006). Any pass that throws still exits 0 to the CLI.
- ADR-027's closed `okf_type` taxonomy. v2 reads `okf_type` but does not add or rename values.

## Open items

- **Whether `Contradiction` taxonomy retention should remain `none` indefinitely.** This ADR sets `Contradiction` to `none` (stays until explicitly resolved). If real-bench measurement shows contradiction observations accumulate without resolution at problematic rates, future work could add a resolution-tracking mechanism (e.g., a separate "contradiction resolved" event that retires the observation card) — separate ADR.
- **Induction's clustering signal.** "Cluster of related cards worth synthesizing" needs a definition: same `okf_type` + same dominant `tags` + similarity above threshold? Open question for PR #3 — first cut uses tag-and-type clustering with a similarity floor; refined empirically.
- **Code-change detection as a retention signal.** Several types in the table above have `none` retention because the *real* signal for staleness is "the code this references has changed." That signal doesn't exist today. Worth its own ADR (storage-domain, since it depends on tracking file/symbol provenance per memory) — out of scope for v2.
- **Recall-utility eviction.** A separate not-yet-implemented mechanism: memories that have not been recalled in K cycles get demoted/deleted. Orthogonal to v2 (could be a fifth pass); separate ADR. Mentioned here only to acknowledge that age is not the only TTL-like signal we might want.
- **Migration of existing stores.** When v2 lands, existing stores have memories all carrying `okf_type: Memory` (pre-V5) or recently-typed V5 memories. The type-conditioned retention table treats `Memory` as 30-day, so pre-V5 memories will age out on the existing schedule — no migration needed. V5+ memories will be properly typed and follow the new table.
- **Per-type LLM model selection.** Induction may benefit from a stronger model than deduction (synthesis is harder than judgment). Optional future enhancement — v2 ships with one model for both, configurable separately if measurement supports the split.
