# Suggestions for the memory team — closing the LongMemEval plugin gap

These are improvement ideas for the **memory mechanism** (the team's parallel work:
`eval/memeval/stores/**`, `okf.py`, `router.py`, `protocols.py`, the Claude Code
service/server, and the `InMemoryStore.search` scorer in `harness.py`). They are
**prose recommendations only** — no code in this file touches the memory mechanism.
The benchmark pipeline (grader/metrics) was fixed separately for *correctness* and
does not change the memory behavior being measured.

## What we measured (the honest baseline)

LongMemEval, Claude Code subscription, `claude-haiku-4-5`, n=20, plugin reached
memory 20/20 after the MCP startup-race fix:

| metric | builtin (Claude Code native files + grep) | plugin pre-BM25 (v0.1) | plugin **BM25** (`results/v0.1-bm25/`) |
|---|---:|---:|---:|
| accuracy | **0.35** | 0.20 | 0.25 |
| relevancy | n/a (native memory) | 0.005 | 0.57 |
| recency | n/a | 0.75 | 0.84 |

Failure breakdown of the 20-task plugin run:

- **1** recall miss (gold not retrieved)
- **3** answer-not-in-content (the gold session genuinely doesn't contain the answer)
- **9** gold retrieved **and** the answer is present in the retrieved text, but the
  model still answered wrong
- **0** grading errors
- **~4** correct

Two facts follow, and they shape every suggestion below:

1. **Recall is NOT the bottleneck.** Gold was actually retrieved in **~12/15** reached
   tasks (raw id-match; the earlier "0/15" was a measurement artifact — `is_gold` was
   never persisted to the logged JSONL, since fixed). The `recency = 0.75 → 0.84`
   already proved gold *was* being surfaced.
2. **The binding constraint is long-context answer EXTRACTION.** Memory items are
   *whole sessions* of 3k–9k characters. In 9/20 cases the answer is sitting inside
   the retrieved item but buried in noise, and the model fails to pull it out.
   Builtin wins precisely because `grep` hands the model **small, targeted matched
   lines** instead of a multi-thousand-character session blob.

## Note on BM25 — already merged, do not re-do

BM25 ranking is **already merged (PR #43)** and stays. It did exactly what was
predicted: it moved gold off the ~0.007 Jaccard tie-floor to the top of the ranking
(offline replay: gold recall@5 **12/15 → 15/15**), lifted **relevancy 0.005 → 0.57**
and **recency 0.75 → 0.84** — but only nudged **accuracy 0.20 → 0.25**. That is the
proof that **ranking is not the binding constraint**: even with near-perfect ranking,
accuracy stays below builtin's 0.35 because the answer is still buried in a giant
session blob. Do **not** re-propose BM25 or further ranking tweaks as the headline
lever — they will keep paying off in relevancy/recency but not in accuracy.

## Suggestion 1 (highest leverage): turn-level chunking

**Store small, per-turn memory items instead of whole sessions.**

This is the single highest-leverage change because it directly attacks the dominant
failure class — the **9/20** "gold retrieved + answer present but model still wrong"
cases. The mechanism is simple: today a retrieved item is a whole 3k–9k char session,
so even a perfectly-ranked top hit dumps a wall of text on the model and the answer
is one needle in it. If each memory item were a **single conversational turn** (or a
small sliding window of 1–3 turns), the retrieved item is short and the answer is no
longer buried — the model gets essentially the same small, targeted snippet that
makes `grep`/builtin win.

Concrete pointers (memory-mechanism side, the team's to implement):

- When seeding/ingesting sessions, split each session into per-turn `MemoryItem`s
  rather than one item per session. Keep a `session_id` (and a turn index) on each
  chunk so provenance and ordering are preserved and gold attribution still works
  against the benchmark's `answer_session_ids`.
- Gold annotation in the pipeline already keys on `gold_memory_ids`; if a chunk
  carries its parent `session_id` the existing `is_gold` logic continues to work
  (a chunk whose parent session is gold is gold). The team should decide whether
  gold is "any chunk of the gold session" or "the specific gold turn" — the latter
  is a stricter, more informative signal.
- BM25 is *more* effective on short turns than on long sessions (IDF discrimination
  is sharper when documents are short and topically tight), so this compounds with
  the already-merged #43 rather than competing with it.

Expected effect: this is the change that should actually move **accuracy** toward
(and past) the builtin 0.35, because it removes the extraction barrier that ranking
alone cannot.

## Suggestion 2: grep-style literal recall query

**Make the recall query closer to grep-style literal matching.**

Builtin's advantage is partly that `grep` matches the *literal* salient tokens of the
question against the haystack and returns the matching lines. The plugin's recall
currently leans on lexical BM25 over the question text, which is good for ranking but
still returns whole items. A recall path that (a) extracts the high-salience literal
tokens / named entities from the question and (b) prefers items containing those exact
tokens would narrow candidates the way `grep` does. Combined with Suggestion 1
(short items), a literal-token recall would surface the exact turn containing the
answer. This is a smaller, complementary win — useful, but secondary to chunking.

## Suggestion 3: per-item snippet / summary fed to the model

**Feed the model a small per-item summary or extracted snippet, not the full session.**

If turn-level chunking (Suggestion 1) is not adopted immediately, an interim measure
is to attach a short summary or a query-focused extracted snippet to each large memory
item and hand the model **that** (3–9k chars → a few hundred), reserving the full
session for provenance/expansion only. This mitigates the extraction barrier without
re-architecting ingestion. It is strictly an interim measure: chunking is cleaner and
avoids a summarization step that can itself drop the answer. If a summary is used, it
must be generated deterministically/offline-friendly (no extra LLM call in the hot
path that would break reproducibility of the benchmark).

## Out of scope / explicitly NOT recommended

- **An LLM-judge QA grader.** The deterministic grader has known residual limits
  (negation/list false positives like "your name was *not* Johnson" still credit the
  gold token; paraphrase/synonym/abbreviation false negatives like "ten" vs "10").
  An LLM judge would address some of these but is **non-deterministic** and would make
  the benchmark unreproducible. We tightened the grader from raw-substring to
  whole-word containment (eliminating the digit-inside-a-number false positives, e.g.
  gold `7` no longer matching "17 shirts") and documented the rest as known
  limitations rather than papering over them. Keep the grader deterministic.
- **Further ranking tuning as the headline fix** (see the BM25 note above).

## Suggestion 4: let priming run on stdio / WSL, not native-only (recall reliability)

**The priming + retry path is gated to native runtimes, so every WSL plugin run —
both QA and CODE — silently loses ~half its recalls.**

`_solve_plugin` routes to the primed HTTP path only when
`self.transport == 'http' and rt.kind == 'native'`
(`eval/memeval/claudecode/agent.py`, the dispatch in `_solve_plugin`). On this
machine the artifacts are produced under a **WSL** runtime (the `.mcp.json` command
is `/home/kenhu/.venvs/swebench/bin/python`), so `rt.kind == 'wsl'`, the condition
is false, and the QA plugin path falls through to a **plain, non-primed**
spawn-per-invocation stdio turn. Plain headless `claude -p` registers the MCP
server only ~half the time before generation starts (the documented 8/20-vs-20/20
startup race), so `memory_recall` is silently unavailable on roughly half of WSL
plugin turns — QA and CODE alike. This is the mechanism-side root of the
`memory_reached = 0` CODE blocker; we worked around it on the **pipeline** side for
CODE by driving the agentic CODE plugin turn through `_run_primed` + retry-until-
recall directly (it already supports WSL via `build_argv_primed` and gates priming
on `self._runner is run_claude`, not on `rt.kind`), but the QA plugin path on WSL is
still unprimed.

Suggested mechanism change (team-owned — we did not edit it): gate priming on the
**runner identity** (`self._runner is run_claude`), not on `rt.kind == 'native'`, so
stdio/WSL plugin runs get the priming turn too. `_run_primed` and `build_argv_primed`
already have working WSL branches, so the priming flow itself needs no new platform
code — only the dispatch condition. (Open question worth confirming first: was the
HTTP/native gate deliberate to avoid a known Windows↔WSL HTTP/port issue, or just an
oversight? If HTTP is genuinely problematic across the boundary, the fix is to allow
the **stdio + priming** form on WSL rather than the HTTP form.)

## Suggestion 5: swe_contextbench loads tasks with `n_gold = 0` (metric-quality gap)

**Even once recall fires on the CODE path, contextbench precision/relevancy stay 0
because the loaded tasks carry no gold ids.**

`swe_contextbench` tasks are loaded with `n_gold = 0`, so the retrieval-quality
metrics (precision / relevancy) have no gold set to score against and read 0
regardless of what the agent actually retrieved. This is independent of the
`memory_reached` fix: making recall fire proves the mechanism is *exercised*
(`memory_reached > 0`, a `retrieve` step is emitted) but does **not** make
contextbench's memory-quality numbers meaningful. Surfacing gold memory ids for
contextbench tasks (so `n_gold > 0`) is a separate, mechanism-/loader-side metric
fix the team should track distinctly from the recall-reliability work above.

## Suggestion 6: plugin hooks hardcode `python3 -m cookbook_memory` → silent no-op off-venv

**Keith's plugin (`plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json`)
wires every lifecycle hook as `python3 -m cookbook_memory.adapters.claude_code.hooks_handler <Event>`.
This silently disables all memory hooks whenever the `python3` Claude Code resolves
can't import `cookbook_memory` — which is the normal case when the plugin is installed
into a venv.**

All five hooks (`SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`,
`PostCompact`) use a bare `python3 -m cookbook_memory...`. That assumes (a) `python3`
is on Claude Code's `PATH` and (b) the `cookbook_memory` package is importable by
*that* interpreter. When the plugin is `pip install`-ed into a virtualenv (the
documented `pip install -e 'plugin[mcp]'`) but Claude Code's `PATH` resolves to a
*different* `python3` (the system one), `python3 -m cookbook_memory...` exits non-zero
with `No module named 'cookbook_memory'`.

Crucially this fails **silently**: a `SessionStart` hook exiting non-zero is
non-blocking, so Claude Code continues normally — but the hook's work (the `note`
event, and on `Stop`/`PreCompact` the Daydream extraction subprocess) never runs, with
**no error surfaced to the user**. The symptom is "memory features quietly don't work,"
which is far harder to diagnose than a loud failure. (Verified directly: under the
system `python3` the `SessionStart` hook errors with `ModuleNotFoundError`, the session
still completes, and no hook output is produced; under the venv `python3` the same hook
returns cleanly with a `hook_response` event.)

Suggested plugin-side fix (Keith's to implement — we did not edit the plugin):

- Don't invoke a bare `python3 -m`. The plugin already ships a **`memory-hook`
  console script** (it lands on `PATH` at install with the correct interpreter
  shebang). Changing the hook commands to `memory-hook <Event>` makes them resolve to
  the same interpreter the package was installed into, regardless of Claude Code's
  `PATH`. Alternatives: invoke via `${CLAUDE_PLUGIN_ROOT}`-relative path, or have a
  thin launcher resolve `sys.executable`.
- Optionally, make a failed hook *loud* rather than silent (the handler already
  fail-opens; a one-line stderr when the import fails would turn "memory mysteriously
  off" into an actionable message), mirroring the existing `daydream-cli not on PATH`
  stderr note.

Note (pipeline side, already fixed — context only): this hook issue is **not** what
blocks a `plugin-real` benchmark run. The blocker was that the isolated
`CLAUDE_CONFIG_DIR` sandbox was never authenticated (`"Not logged in · Please run
/login"`); the harness now seeds the host subscription into the sandbox in
`sandbox.setup_real_plugin` (`seed_auth_from_host`, pipeline-owned). The hook fix above
is a separate robustness issue in the plugin that affects whether memory hooks actually
fire once the plugin *does* run.

## Evidence index

- Corrected recall diagnosis and the metric table: `results/v0.1/README.md`.
- Live BM25 re-run records (n=20, reach 20/20): `results/v0.1-bm25/`.
- BM25 scorer change: PR #43 (merged), guarded by the `test_bm25_*` tests in
  `eval/tests/test_smoke.py`.
- `is_gold`-persistence fix that corrected the recall measurement: PR #46.
- CODE `memory_reached = 0` blocker diagnosis (9-agent investigate → verify):
  `code-memory-reached-blocker-and-plan.md` (repo root).

## Daydream LLM 429s are dropped without retry (`eval/memeval/dreaming/llm.py`)

**Symptom.** In a `plugin-real` SWE-Bench-CL run (3 django tasks, paid model
`qwen/qwen3-next-80b-a3b-instruct`), the daydream events showed `daydream.memory_written: 7`
but also `llm_rate_limited (429): 3` + `chunk_skipped_unavailable_llm: 3` — 3 memory chunks
were lost to transient 429s in a single run.

**Root cause.** `OpenRouterClient.complete()` makes a single `httpx.post` and, on HTTP 429,
emits `llm_rate_limited` and returns an empty `Completion` immediately (lines ~231–239).
There is **no retry, no backoff, and no `Retry-After` handling**. The engine then treats the
empty completion as `chunk_skipped_unavailable_llm` and (per ADR-dreaming-013) does not
advance the cursor — so the chunk is retried only on a *later* daydream pass, not within the
run. Any single transient 429 drops that chunk's memory for the run.

**The 429s are transient, not an account/credit limit.** Reproduction the same day:
- 8 rapid back-to-back calls to the **paid** `qwen/qwen3-next-80b-a3b-instruct` → all `200`.
- `GET https://openrouter.ai/api/v1/key` → `limit: 10`, `limit_remaining: 9.99`, `usage: 0.006`.
So credits/account are healthy. The run's 429s are upstream-provider tokens-per-minute spikes
under the daydream's real payloads (`DEFAULT_MAX_TOKENS = 4096` + a redacted transcript chunk),
which a 16-token probe can't reproduce. A bounded retry would clear essentially all of them.

**Suggested plugin/team-side fix (we did not edit `eval/memeval/dreaming/**`):**
1. **Bounded retry with exponential backoff on 429 (and 5xx)**, honoring the `Retry-After`
   response header when present, e.g. up to 3 attempts with `0.5s · 2^n` (+jitter) capped at a
   few seconds. Keep the existing fail-open: after the final attempt, still emit
   `llm_rate_limited` and return an empty `Completion` (no behavior change on permanent limits).
2. **Optional — OpenRouter provider routing.** Add a `provider` block to the request body so
   OpenRouter can route around a rate-limited upstream, e.g. `"provider": {"sort": "throughput"}`
   or rely on default `allow_fallbacks`. This reduces 429s before retries are even needed.
3. **Capture the 429 detail** (the `Retry-After` header and a short body snippet) in the
   `llm_rate_limited` event — currently only `status` is recorded, which hides whether the limit
   is per-minute vs daily and how long to wait.

**Why this can't be fixed harness-side.** The daydream LLM client is constructed inside the
`daydream-cli` subprocess fired by the plugin's Stop hook; the harness only sets env
(`DREAM_PROVIDER`, `DREAM_MODEL`, `OPENROUTER_API_KEY`) and there is no retry env knob, so the
retry must live in `OpenRouterClient.complete()`.

Evidence: run `eval/runs/swe_bench_cl_pluginreal_smoke/v0.1/plugin-real/_groupstore/django_django_sequence/dream/*.daydream-events.jsonl`
(`llm_rate_limited` × 3); the burst reproduction + `/api/v1/key` output above.
