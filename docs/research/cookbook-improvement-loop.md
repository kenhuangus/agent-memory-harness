# Cookbook-memory improvement loop (gated)

**Goal.** Make the cookbook-memory plugin deliver **≥30% more resolved tasks than
`max(builtin, off)`** for **each** coding CLI — **claude, grok, agy** — on the SWE-bench-CL
sequences. "builtin" = the CLI's native memory; "off" = no memory.

**Why gated.** A full Tier-3 measurement is hours of solver+grader time and money, and n=10
is ±2 noise. So every change is screened by two fast offline gates first. Tier 1/2 are
*necessary, not sufficient*: they can't prove a solve-rate lift (only Tier 3 measures that),
but a change that fails them cannot plausibly produce +30%, so it is rejected without
spending Tier 3.

## The loop (one iteration)

1. **Propose a cookbook *code* change** (a real diff in `plugin/cookbook_memory/**`,
   `eval/memeval/stores/**`, or `eval/memeval/dreaming/**`) targeting recall precision,
   injection discipline, ranking, or consolidation quality.
2. **Tier 1 — retrieval-precision gate** (seconds–minutes, ~free). Rebuild a populated
   sympy store under the candidate code, recall every real task query (k=5), LLM-judge each
   hit's relevance, compute precision@5 / MRR / useful-hit-rate.
   **APPROVE iff** useful-hit-rate ≥ 0.60 **and** precision@5 ≥ baseline + 0.05
   (i.e. measurably better than the current code; off/builtin inject no cookbook hit, so any
   relevant hit is net-new signal). Else **reject — do not run Tier 3.**
3. **Tier 2 — consolidation-quality gate** (seconds–minutes, ~free). Run the dream worker
   over a fixed set of sympy sessions; LLM-judge extracted memories for faithfulness
   (entailed by the session, not hallucinated) and transferability (reusable
   Invariant/Convention/Fix). Measure yield.
   **APPROVE iff** faithfulness ≥ 0.90 **and** yield ≥ 1/session **and**
   transferable-fraction ≥ 0.50 **and** ≥ current-code baseline. Else **reject.**
4. **Tier 3 — solve-rate confirmation** (hours, $$). ONLY if Tier 1 AND Tier 2 approve.
   Run claude + grok + agy, each `base` / `builtin` / `plugin`, on a task set with
   **headroom** (enough / hard enough tasks that base does NOT saturate — grok hit 10/10 on
   10 tasks, so 10 is too few). Cookbook must be **≥30% over max(builtin, off) for each CLI.**
5. **Land it.** If Tier 3 meets the bar → write an **ADR** describing the change + the
   measured deltas, open a **PR** (code + tests + ADR), route to the code owner. If not →
   record the result, propose the next code change, loop.

## Gate tooling

- Tier 1: `eval/tools/recall_precision_gate.py` (rebuilds store under candidate embedder via
  `rebuild_store`, recalls real queries, `--judge` precision via `eval/tools/recall_eval.py`
  machinery). Baseline + candidate compared in one run.
- Tier 2: `eval/tools/consolidation_gate.py` (dream worker over fixed sessions, LLM-judge
  faithfulness/transfer/yield).
- Tier 3: `runs/<set>-{claude,grok,agy}/driver.sh` (3 arms each), subscription for claude,
  OpenRouter for grok, agy CLI for agy.

## Fixtures

- Populated sympy store: `results/vsympy_sympy_sequence-plugin-dreamed-8c48b84-1/_memory/.cookbook-memory/`
- Real task queries: the `recall` events in that run's `events.jsonl`.

## Honest caveats

- The 30% target across all three CLIs is research-grade; baselines currently show cookbook
  at/below par. Tier 1/2 raise the odds and prevent wasted Tier-3 spend; they do not promise
  the outcome.
- A relevant memory recalled is necessary but not sufficient — the solver must also *use* it;
  that solver-conditioned conversion is exactly what Tier 3 measures per-CLI.
