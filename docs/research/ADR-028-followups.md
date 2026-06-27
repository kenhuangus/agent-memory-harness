# ADR-028 followups — deferred work in dream worker v2

**Status:** None of the items below are committed implementation work. They are
*deferred design directions* preserved here so that future contributors (or
future-us) can re-engage them with full context. v2 as actually shipped covers
ADR-028 §1 (type-conditioned retention) and §2 (streaming neighborhood
consolidation, including LLM dedup); §3 and §4 from the umbrella ADR were
written into the decision document but never reached implementation, and the
team chose to flip §2's v2 paths to default ON via PR #2h
([dreaming/v2-flip-defaults-on](https://github.com/kenhuangus/agent-memory-harness/pull/229))
without an A/B. This document captures what's left, what each item would buy,
and what would justify revisiting.

For the binding decisions on §1 and §2 see
[`docs/adrs/ADR-dreaming-028-dream-worker-v2-redesign.md`](../adrs/ADR-dreaming-028-dream-worker-v2-redesign.md);
for §5 (the OKF taxonomy amendment adding `Contradiction`) see
[`docs/adrs/ADR-dreaming-027-okf-content-type-taxonomy.md`](../adrs/ADR-dreaming-027-okf-content-type-taxonomy.md)
as amended by ADR-028 §5.

---

## §3 — Deduction / induction specialist split

### What it is

Today's consolidation cycle has one mechanism with one authority surface: the
dream worker deletes items it judges as duplicates, contradictions, or
governance-blacklisted. The deduction/induction split divides this into two
distinct passes with different authority:

- **Deduction (the "cleaner")** — DELETE-authority pass. Takes each item's
  K=10 neighborhood (the same stack PR #2c-2f use), runs lexical dedup, runs
  an LLM judgment. The LLM returns one of four NOOP-aware operations:
  - `NOOP` — nothing to do
  - `UPDATE` — the loser is a stale version of the winner; delete the loser
  - `DELETE` — exact duplicate after normalization; delete the loser
  - `CONTRADICTION` — flat disagreement (handed off to §4 below)

  Type-conditioned retention (already shipped in PR #1) runs first within the
  deduction pass, same in-flock guarantees as today per
  [ADR-dreaming-021](../adrs/ADR-dreaming-021-basedir-flock.md).

- **Induction (the "generalizer")** — CREATE-only pass. After deduction
  completes, reads a recent slice of post-deduction survivors looking for
  clusters of structurally related cards. Emits new synthesized cards typed
  as `Invariant` or `Convention`, with provenance metadata
  (`metadata.synthesized_from: [item_id, ...]`) naming the source items.
  Cannot delete, cannot touch the deduction's working set, capped by a
  separate per-run LLM call budget.

The authority split is the load-bearing piece: one mechanism gets the keys to
delete, another gets the keys to create — neither can do both. Today's dream
worker has the delete authority and no create authority. The induction pass
would close that asymmetry.

### Citation chain

The split mirrors [Honcho](https://honcho.dev)'s architecture. ADR-028 §Rationale
records the verification done at decision-time:

> Honcho's confirmed implementation pattern (deduction has delete authority over
> the peer-card; induction is creation-only and requires ≥2 source observations
> per emit) is the template; we adopt the authority split and the
> provenance-on-create requirement. (ADR-028 §Rationale,
> [`docs/adrs/ADR-dreaming-028-dream-worker-v2-redesign.md`](../adrs/ADR-dreaming-028-dream-worker-v2-redesign.md))

ADR-028 cites Honcho's `agent_tools.py:447` as the verification site for the
peer-card authority claim. Anyone revisiting this should re-verify the cite
against current Honcho — the file may have moved since ADR-028 was written.
Honcho is open-source at <https://github.com/plastic-labs/honcho>.

The four-operation NOOP-aware schema (NOOP / UPDATE / DELETE / CONTRADICTION) is
Mem0's published pattern; see [Mem0's documentation](https://docs.mem0.ai) for
the prompt shape Mem0 uses to drive that judgment. Our `_detect_duplicates_neighborhood`
(PR #2e) currently uses a simpler "are these pairs the same thing?" prompt
without the UPDATE/CONTRADICTION distinction inside the dedup call — §3 would
extend the prompt to return all four cases.

### Hypothesis for benefit

Two hypotheses, distinct enough to test separately if a future bench measures
this:

1. **Authority separation reduces silent-merge defects.** Today's single-pass
   worker, when the LLM judges a pair as similar, deletes the loser regardless
   of whether the relationship was UPDATE (loser is stale; delete is correct) or
   CONTRADICTION (loser disagrees; delete loses information). The shipped v2
   dedup prompt (`DEDUP_SYSTEM_PROMPT`) explicitly excludes contradictions from
   the dedup criterion ([PR #223 CodeRabbit fix](https://github.com/kenhuangus/agent-memory-harness/pull/223)),
   so the worst case is already prevented — but the prompt asks the LLM to
   *self-police* the distinction. The deduction specialist with explicit
   four-class output would surface the case to the worker rather than relying on
   the LLM to refuse to merge it. **Intuition, not verified:** structural
   guards beat prompt-level rules at scale.

2. **Induction unlocks upgrade-by-pattern that the system currently can't
   express.** Today, three separate `Fix` cards for structurally similar bugs
   stay as three `Fix` cards. The induction pass could synthesize them into one
   `Invariant` ("django's queryset cloning needs to follow this pattern") with
   provenance pointing to the three originals. The originals would then age out
   under the 90-day `Fix` retention while the `Invariant` (no calendar TTL by
   ADR-028 §1) carries the lesson forward. **Hypothesis:** higher Memory-F1 on
   benchmarks that ask about similar-but-not-identical past work, because the
   recall surface includes a generalized lesson the cleaner-only pipeline can
   never produce.

### Risks

- **Hallucination on synthesis.** Induction is creation-from-LLM-inference; the
  LLM could write a synthesized `Invariant` that overgeneralizes from its
  sources. ADR-028's mitigation is mandatory provenance metadata so operators
  can audit. The team would also want a much smaller call budget at first.
- **Cost compounding.** Adds another LLM pass to the consolidation cycle on top
  of the dedup+contradiction passes that already shipped. Per PR #2c's cost
  analysis, neighborhood-scoped passes scale with #pivots × K — induction would
  add another factor.
- **Authority boundary discipline.** "Induction cannot delete" needs to be
  enforced at the store-call level, not just by convention. A bug that lets
  induction call `store.delete` would defeat the whole authority split.

### What would justify revisiting

A real-bench result showing one of:

- Memory-F1 plateau on tasks that *should* benefit from generalization (e.g.,
  Django sequence tasks where past-fix patterns recur). If the F1 ceiling
  appears to be hit by individual-card recall, induction is the most plausible
  unlock.
- Operator-visible silent-merge defects from the shipped dedup pass that the
  prompt-level guard fails to catch.

---

## §4 — Contradiction-as-data with provenance

### What it is

Today's contradiction pass (both v1 and the neighborhood-scoped v2 from PR #2c)
identifies disagreeing pairs and **deletes the loser** (latest-timestamp wins,
lex tiebreak). The disagreement signal is destroyed in the act of resolving it.

§4 inverts the default: when the LLM judges a pair as `CONTRADICTION` (per the
§3 four-class schema), the worker does NOT pick a winner. Instead:

1. Both source items are preserved (no delete).
2. A new `Contradiction`-typed memory is written with content recording the
   disagreement, and metadata structured as:

   ```yaml
   okf_type: Contradiction
   metadata:
     contradicts: [<item_id_a>, <item_id_b>]
     resolved: false
     rationale: <LLM-provided ~200 char explanation>
     detected_at: <timestamp>
   ```

3. The `Contradiction` card itself has no calendar retention (per PR #1's
   type-conditioned retention table). It stays as data until explicitly
   resolved.

The `Contradiction` OKF type is already reserved by PR #0
([ADR-dreaming-027 §amended](../adrs/ADR-dreaming-027-okf-content-type-taxonomy.md)
+ ADR-028 §5) and listed in `OKF_CONTENT_TYPES` and `TYPE_RETENTION_DAYS`. The
type slot is real; what's missing is the worker code that emits one. Today the
slot is **dormant** — no code path currently writes a `Contradiction` memory.

### Citation chain

ADR-028 §Rationale credits Honcho:

> Honcho's specialist drew the distinction we collapsed (verified in
> `agent_tools.py:447`): updates delete the loser, true contradictions emit a
> new contradiction-level observation that preserves both originals as data.
> (ADR-028 §Context #3)

The "emit-rather-than-delete" pattern for disagreements is the Honcho-specific
contribution; the broader "memory-as-data" framing is also discussed in
[Letta](https://letta.com)'s public materials, where they argue for explicit
operations over implicit state changes. ADR-028 references Letta as one of the
three community projects surveyed; specific Letta code citations were not
recorded in the ADR and would need fresh verification if this becomes
implementation work.

### Hypothesis for benefit

1. **Operational observability of disagreements.** Today, an operator looking
   at the inspector UI sees only the survivors. A disagreement between two
   memories was resolved silently three days ago and is unrecoverable. With §4,
   `Contradiction` cards are queryable in the inspector: "show me everywhere
   two memories actively disagreed in the last 30 days." That's a real new
   capability for debugging memory quality. **Hypothesis:** operators catch
   incorrect contradiction-pass resolutions sooner because the original signal
   is visible.

2. **Future human-in-the-loop resolution.** With contradictions preserved as
   data, a future workflow can route them to a human (or to a more careful
   model) for resolution. Today's silent-loser-delete prevents this entirely.
   This is forward-leaning, not a benefit available at §4 ship-time.

3. **No information loss when the LLM is wrong about which side to delete.**
   The current contradiction pass uses `_pick_winner` (latest timestamp;
   ADR-dreaming-022) to deterministically pick the loser. This is the right
   call most of the time but it's not LLM-correctness — it's a heuristic. If
   the older item was actually the correct one, the loss is permanent.
   Preserving both sides makes the heuristic recoverable.

### Risks

- **Store size growth.** Contradictions accumulate without calendar TTL.
  Mitigation: the worker can later add a "resolved" status and reap resolved
  contradictions, or a `Contradiction`-specific count cap. Neither is essential
  at v3 launch.
- **Recall surface noise.** A `Contradiction` card surfaces in `search()`. If
  the recall caller sees the contradiction in a search result, they need to
  know how to handle it ("here are two memories that disagree"). The recall
  side has no current handling for this content type. May need a recall-layer
  filter or an explicit consumer surface.
- **Backwards compatibility.** Existing inspector UIs would need to render
  `Contradiction` cards distinctively. This is small but real frontend work.
- **The taxonomy slot is already reserved**, so §4 doesn't require coordinating
  with the storage workstream on schema — the type passes through the existing
  metadata machinery cleanly.

### What would justify revisiting

- An operator-side incident where a wrong contradiction-pass resolution caused
  a real problem and the team realized the original signal was unrecoverable.
- A bench-time effort to quantify how often the contradiction pass picks the
  wrong winner (this would itself benefit from a contradiction-preservation
  prototype to A/B against).
- Movement toward a human-in-the-loop memory governance surface where
  `Contradiction` cards become the primary input.

---

## Other potential followups (smaller / orthogonal)

### Separate `DREAM_DEDUP_MAX_CALLS` budget knob

Today the LLM dedup pre-pass (PR #2e/f) shares `DREAM_CONTRADICTION_MAX_CALLS`
as its budget cap. Tuning the two independently would require a separate env
var. Deferred because:

- The shipped wiring is one variable, simpler operator surface.
- We have no A/B data showing the two need independent tuning.

Trigger to revisit: a bench run where the optimal pivot count for dedup is
different from the optimal pivot count for contradiction (likely if paraphrase
density and disagreement density vary independently across workloads).

### A/B measurement of the flip-on-trust default

PR #2h made `DREAM_CONTRADICTION_NEIGHBORHOOD` and `DREAM_DEDUP_NEIGHBORHOOD`
default ON without measurement. The deferred work is the 2×2 grid the PR body
described:

|                              | v1 contradiction | v2 contradiction |
|------------------------------|------------------|------------------|
| **Lexical-only dedup**       | today's baseline | contradiction-only v2 |
| **Lexical + LLM dedup**      | dedup-only v2    | **current default** |

Metrics: Memory Recall, Memory-F1, $/run, LLM calls/run, p95 latency. 3–5 runs
per cell. Lands as an ADR-level note pinning numbers; if the bottom-right cell
regresses on F1, the kill switches (`DREAM_CONTRADICTION_NEIGHBORHOOD=0`,
`DREAM_DEDUP_NEIGHBORHOOD=0`) revert without code change.

This isn't in §3 or §4; it's measurement debt that the flip-on-trust decision
explicitly accepted.

### Provenance audit UI

If §3 ships, the inspector UI needs a way to display the `synthesized_from`
chain — clicking a synthesized `Invariant` should let the operator see the
3+ source `Fix` cards it was built from. Without that, induction's
hallucination-mitigation story is incomplete. The frontend work is small; the
backend already passes metadata through.

This is contingent on §3 — no value to ship before induction exists.

### Cross-pivot judgment caching

The neighborhood-scoped passes (§2 PR #2c, #2e) call `store.search()` once per
pivot. If pivots A and B are each other's nearest neighbor, the search work is
redundant — both calls compute essentially the same K nearest. A pivot-index
cache that memoizes "I've already judged the pair (A, B) this run" would save
search cost (already done via `frozenset({a_id, b_id})` in the existing helpers)
*and* search calls. The current code dedupes the LLM judgment but not the search
itself.

Trigger to revisit: a profiling run showing `store.search` as the
consolidation-cycle hot path. Today's profiles haven't shown that, but they
were measured on small stores.

### Per-type retention introspection

PR #1 made the retention table a code-level constant
(`TYPE_RETENTION_DAYS` in `worker.py`). It's not exposed at runtime — operators
can't query "what's the current Fix retention?" without reading the source. A
small `dream worker introspect` CLI command (or a JSON dump on cycle start) would
help debugging when retention behavior seems off.

Trigger to revisit: an incident where retention shadow-changed (e.g., a
mismatch between deployed code and operator expectations).

### Replace `_pick_winner` with type-aware loser selection

`_pick_winner` (ADR-dreaming-022) uses latest-timestamp + lex tiebreak for both
dedup and contradiction loser selection. For `Decision` and `Preference` types
this is probably correct (recency wins). For `Identity` it might be wrong
(stability should win over churn). A type-aware loser-selection function would
let the policy match the retention semantics from PR #1.

Marginal. Deferred until a real example of "wrong loser kept" comes from a
bench.

### Reaper for resolved `Contradiction` cards

Contingent on §4. If `Contradiction` cards have no calendar TTL, they need
some other reap mechanism eventually: explicit operator resolution, a "resolved
by superseding write" detector, or a count cap. Open design question; not
needed at §4 ship-time but real before the store fills up with stale
contradictions.

---

## Decision log: why none of this is implementation work right now

1. **The team explicitly chose flip-on-trust over the A/B path** for the v2
   defaults. That same trust budget is being spent on §2 going live; spending
   it again on §3 or §4 without measuring §2 first would compound the risk.
2. **§3 in particular is the highest-stakes change** — induction is
   create-from-LLM-inference, the failure mode is silent hallucinated memories.
   Shipping it without bench-time validation of the prompt + provenance
   surface would be irresponsible.
3. **§4 is lower-risk but couples to the recall surface** in ways the team
   hasn't worked out. A `Contradiction` content type that surfaces in
   `search()` results needs a story for how the recall caller handles it.
4. **The smaller followups** mostly trigger on real data (A/B numbers,
   profiling, incidents). None of them have triggers met today.

If any of these change — a bench result that surprises us, an incident, a
profile that points at search cost — re-engage with this document as the
starting point. ADR-028 was the load-bearing decision; this is its companion
"the decisions we deferred."
