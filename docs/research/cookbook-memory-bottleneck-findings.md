# Cookbook-memory bottleneck — findings from the gated improvement loop

**Question.** Can the cookbook-memory plugin be made to resolve **≥30% more tasks
than `max(builtin, off)`** for claude, grok, and agy on the SWE-bench-CL sequences?

**Method.** A three-tier *gated* loop (see `cookbook-improvement-loop.md`): screen every
candidate with two fast offline gates before spending the hours-long, $$ Tier-3 solver
run. Tier-1 = retrieval precision; Tier-2 = consolidation quality; Tier-3 = solve rate.
Tooling: `eval/tools/{recall_harvest,recall_eval,recall_precision_gate,consolidation_gate,extract_ab}.py`.

## Findings

**1. Retrieval is NOT the bottleneck.** On 158 real sympy memories + 18 real task queries,
LLM-judged precision@5 = **0.60 (fusion)** / **0.77 (accuracy/Voyage)**, MRR 0.84–0.92,
useful-hit-rate **1.0**. The relevant memory is almost always surfaced in the top 1–2.
- Corollary: a recall **score floor** is a *dead* lever for the `fusion` profile — its
  scores are compressed (~0.05, no bimodal split) yet its *ranking* is good. The earlier
  "give fusion a 0.15 floor" followup is moot; thresholding fusion scores only starves recall.

**2. Consolidation (extraction V5) is good.** True V5 output on 12 real coding sessions,
judged by a *reliable* judge: **transferable 1.0, actionable 0.9**, properly OKF-typed
(Fix/Bug/Invariant). No V6 was warranted — the gate correctly prevented an unneeded change.

**3. METHOD LESSON (load-bearing): the LLM judge must be pinned.** With `openrouter/auto`
the transferable fraction swung **0.667 → 0.111** across runs — pure model-routing variance,
not signal. Pinning the judge to **`openai/gpt-4o-mini`** (deterministic single-char 1/0)
made the gate trustworthy. *Any* LLM-as-judge gate in this repo must pin a single model;
`auto` routing makes the metric meaningless. (Fixed in the three gate tools.)

**4. Invocation works.** Across sympy plugin runs the agent calls `recall` **~1×/task** and
**~90%** of calls return hits — memories do reach the solver.

## Conclusion

With a trustworthy judge, **every offline component of cookbook memory passes**: retrieval
surfaces relevant memories, consolidation produces transferable+actionable lessons, and the
agent invokes recall and receives hits. Yet measured solve rate was **at/below baseline**
(grok 10/10=builtin on 10 tasks; agy plugin 7 < base 9).

The gap is therefore **not a fixable defect in retrieval, consolidation, or invocation**. It
is structural to the benchmark: **SWE-bench-CL tasks are independent bug fixes**, so a
transferable lesson distilled from task A rarely contains the *specific* fix task B needs —
and recall fires ~once at task start, not at each mid-task decision point. A ≥30% lift from
memory is not realistically reachable on independent-task benchmarks via these levers.

## What would actually move it (for a future, scoped effort)

- **Decision-point / forced recall** (cf. PR #250): recall repeatedly at each
  file-open/diagnose/approach step, not once at start — measured by Tier-3.
- **Push-injection**: inject the top-k memories into the solver's first turn instead of
  relying on a pull-tool call.
- **Stronger generalization**: the induction/generalizer pass (ADR-dreaming-028 §3, PR #243)
  to lift one-off fixes into reusable invariants.
- **Right benchmark**: memory's value appears when tasks **share structure** (repeated
  patterns, an evolving codebase), not on a set of unrelated bug fixes. VISTA-style
  repeated-context sequences are a better testbed for the 30% question than SWE-bench-CL.

## Caveat

This conclusion rests on the **offline gates + the prior empirical baseline**, not a fresh
Tier-3 run (deliberately not spent, per the gated design and a low predicted payoff). The
gate tools make re-checking any future change cheap.
