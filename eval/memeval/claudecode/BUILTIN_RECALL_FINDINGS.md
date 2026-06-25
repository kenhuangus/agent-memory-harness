# Findings: instrumenting the `builtin` (Claude CLI native memory) arm fairly

## How `builtin` mode actually runs

- Dispatch: `ClaudeCodeAgent.solve` -> `memory_mode == "builtin"` branch
  (`eval/memeval/claudecode/agent.py` ~line 444).
- Seed surface (the mechanism the CLI actually reads): `_write_session_files(run_dir, task)`
  (`agent.py` ~line 1399) lays the task's prior sessions out as one Markdown file per
  session under `run_dir/sessions/session_NNNN_<id>.md`, plus a `run_dir/CLAUDE.md`
  pointer that tells the model to grep/read those files. Claude Code auto-loads
  `CLAUDE.md` and uses its NATIVE tools (Grep/Glob/Read) over `sessions/` -- this IS
  the native-memory mechanism (a 200k-token single CLAUDE.md just 400s, hence files).
- The turn runs through `_run(..., --output-format json)`. The json envelope holds only
  the FINAL result (text/tokens), NOT the intermediate tool_use trajectory.
- The CLI also writes a FULL transcript jsonl under
  `<CLAUDE_CONFIG_DIR or sandbox.active_config_dir()>/projects/**/*.jsonl`. That
  transcript records every `tool_use` (Read/Grep/Glob) and its `tool_result`.
  cli.py already locates it: `_project_transcript_root` + `_latest_transcript`.

## Where `plugin-real` gets credited (and why builtin read 0/0)

- `recall_attempted` / `recall_with_hits` are counted from the trajectory's `retrieve`
  steps (`eval/memeval/agent.py` ~line 417: steps where `kind=="retrieve" and s.retrieved`).
- `gold_retrieval_f1` is computed by `VistaNativeEvaluator.score`
  (`eval/memeval/native/evaluators/vista.py`) which pulls `retrieved_texts` from the
  trajectory's `retrieve` steps (`_retrieved`, line 271) and CONTENT-matches them to the
  gold session texts via `_content_match` (line 56) -- id-namespace-independent.
- plugin records retrieve steps via `_attribute_plugin_recalls` (agent.py ~480);
  plugin-real via `_attribute_real_recall` (agent.py ~1145), both calling
  `ctx.record_retrieve(hits, query=...)`.
- The `builtin` branch NEVER calls `ctx.record_retrieve`. So it emits ZERO retrieve
  steps -> `recall_attempted == 0` and `gold_retrieval_f1 == 0.0` PURELY because of
  missing instrumentation, not because native memory did nothing. That is the bug.
- `_seed_vista_sessions` (agent.py ~781) seeds VISTA gold (fact+drift, NOT the injection
  canary) into the COOKBOOK store, guarded to plugin-real+vista. builtin's analog is
  `_write_session_files`, which already lays the SAME sessions out as files the native
  tools read -- so builtin already has SEED PARITY for the legitimate gold content.

## The fair builtin analog implemented

a. SEED parity: already present. `_write_session_files` gives builtin the same prior
   knowledge (all sessions as readable files). The injection canary is present on the
   file path exactly as it is on plugin-real's transcript path -- poisoning_resistance
   stays honest (a leak is scored iff the canary text actually surfaces in recall).

b. RECALL instrumentation (the fix): after the builtin turn, parse the CLI transcript
   jsonl for native-memory READS that surfaced `sessions/` content -- `Read` tool_use of
   a `sessions/*.md` file, and `Grep`/`Glob` whose results reference `sessions/` files --
   and emit a `record_retrieve` step per surfaced session, with the RetrievedItem.content
   set to the ACTUAL surfaced session text (id = the session id when resolvable).
   This makes `recall_attempted`/`with_hits` AND `gold_retrieval_f1` measure for builtin
   the SAME way they do for plugin-real (content-matched gold). New method
   `_attribute_builtin_recall(run_dir, task, ctx, since)`.

   HONESTY: a retrieve step is emitted ONLY when the transcript shows native memory
   actually surfaced session content. If Claude's native search never reads the gold
   file, no hit is emitted and the fair result is a real (now-measured) low number.
   We map a surfaced file back to its session by content (read the file we wrote), so
   the evaluator's content-matcher scores exactly the text the model actually saw.

## Parity vs approximation

This is TRUE parity for the metrics that matter (recall_attempted, recall_with_hits,
gold_retrieval_f1, adaptation_rate): all are content-based in the VISTA evaluator, and we
feed the evaluator the genuine content the native tools surfaced. The one
approximation is that the CLI transcript is a behavioral signal (what files the model
read) rather than an explicit "recall" event -- but since native memory HAS no explicit
recall event (it is grep/read over files), the file-read trajectory IS the native
recall, recorded honestly.
