# agy full50 V5 — RESULT (quota-capped, partial)

Decision (2026-06-27): ship agy as partial; do NOT re-run (gemini quota).

## What happened
- agy WORKS mechanically: fix = pass `--add-dir <checkout>` to agy.exe (proven on a
  trivial reproducer + a 2-task gate, 2/2 resolved with real diffs).
- BUT agy's gemini-3.1-pro hit a usage cap after ~16 solve-loops. base diff_len cliff:
  tasks 1-16 produced ~13 real diffs (535,790,586,1304,3580,700,1344,1345,541,658,458,884,535);
  tasks 17-50 = ALL diff_len=0, and the entire builtin rerun = 0/13 diffs. Sustained 50+ min.
- Therefore base 11/50 is a quota-capped FLOOR (real ability higher); builtin/plugin NOT obtainable on this quota.
- The agy 0-diff was NOT a prompt/builtin-memory bug (with empty sessions the builtin prompt
  equals base, yet both die after ~task 16). Root cause = gemini Pro quota exhaustion.
- Separate minor issue: intermittent github SSL checkout failures (~2 in base) = network, not agy.

## Code fix kept
runs/sympy3-agy/agy_runner.py: added `--add-dir "{win_cwd}"` (real fix); builtin/plugin now
inject memory as plain prompt text (no CLAUDE.md/session files written into checkout).

## To get a clean agy 3-arm result later
Re-run base+builtin+plugin when the gemini quota is fresh, throttled (delay between tasks)
to avoid re-exhausting ~16 Pro calls in.
