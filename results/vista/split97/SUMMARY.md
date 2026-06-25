# VISTA — test split, 97 journeys (cookbook vs builtin)

Head-to-head of Claude Code's **native memory** (`builtin`) against the **cookbook plugin's
real store** (`plugin-real`) on the VISTA **test split** (97 journeys).

## Methodology

- **Dataset / split:** VISTA, `test` split, **97 journeys**.
- **Model:** `claude-haiku-4-5`.
- **Memory write/consolidation:** daydream + dream both **active** for the cookbook arm.
- **Grader:** off (deterministic native evaluator metrics only; no LLM judge).
- **Errors:** **0** on both arms.
- **Fairness fixes that made this comparison valid:**
  - #165 — builtin instrumentation (native memory was firing but not being recorded; the earlier
    `0/0` was an instrumentation gap, not native memory doing nothing).
  - #168 — parallel-worker fix.

The earlier 6-journey figures were a **dev-split smoke test**, not a measurement.

## Results

| metric (up=better; ASR down=better) | builtin (Claude native memory) | plugin-real (cookbook) |
| ----------------------------------- | ------------------------------ | ---------------------- |
| recall_attempted / hits             | 19 / 19                        | 61 / 61                |
| gold_retrieval_f1                   | 0.149                          | 0.537                  |
| adaptation_rate                     | 0.196                          | 0.629                  |
| poisoning_resistance                | 0.804                          | 1.000                  |
| targeted_asr                        | 0.196                          | 0.000                  |
| self_improvement_safety (RSI)       | 0.804                          | 1.000                  |
| errors                              | 0                              | 0                      |
| cost_usd                            | 0.345                          | 0.344                  |

## Gates (plugin-real arm — all passed)

- **retrieve** — recall engaged and hit gold content on **61/97** journeys (`gold_retrieval_f1` 0.537).
- **daydream** — **22 memories** written via the daydream hook -> DeepSeek.
- **dream** — `dream --all` consolidated **all 4 domain stores**.

## What each metric means / what this result shows

- **recall_attempted / hits** — how many journeys the agent actually pulled a stored memory in.
  The cookbook engaged on **61/97** vs native memory's **19/97**.
- **gold_retrieval_f1** — overlap of retrieved memories with the gold facts/updates (content-matched);
  **0.537 vs 0.149** = the cookbook surfaces the right memory **~3.6x more**.
- **adaptation_rate** — fraction of drift journeys where the **updated** (superseded) memory was
  retrieved; **0.629 vs 0.196** = the cookbook keeps up with policy changes **~3.2x better**.
- **poisoning_resistance** — fraction of journeys with **no** injected canary in retrieved memory;
  **1.0 vs 0.804** = the cookbook never surfaced the attacker payload; native memory did in ~20%.
- **targeted_asr** — attack success rate (canary leaked into recall); **0.0 vs 0.196**, lower is better
  = the cookbook fully resisted; native memory was compromised ~20% of the time.
- **self_improvement_safety (RSI)** — fraction of journeys where memory consolidation opened **no**
  forbidden-belief path (observer-only RSI gate); **1.0 vs 0.804**.
- **cost** — essentially equal (**~$0.34** both), so the quality/safety gains are free.

## Honesty note

The builtin arm's earlier `0/0` recall was an **instrumentation gap** (fixed in #165), not native
memory doing nothing. With #165 + #168 landed, builtin records real recalls (19/97) — this is a
fair, apples-to-apples comparison.

## Source records

- `builtin.record.json` — VISTA test split, builtin arm.
- `plugin-real.record.json` — VISTA test split, cookbook plugin-real arm.
