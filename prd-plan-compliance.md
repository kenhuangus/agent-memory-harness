# PRD / Plan Compliance Audit — agent-memory-harness

**Audit date:** 2026-06-22 · **Repo HEAD:** `ebf6e2f` (main) · **Branch:** `docs/prd-plan-compliance`

Evidence-backed gap analysis comparing the **current codebase** against the
**original PRD** (`prd.md`), **plan** (`plan.md`, `project-plan.md`), and the
**intended architecture/contracts** (`architecture.md`, `implementation.html`).

Evidence links are GitHub blob URLs pinned to `main`. "A file exists" is never
treated as "the requirement is met" — each verdict is grounded in observed behavior.

---

## Update (post-#62, 2026-06-22)

> **This audit predates the Docker removal — its Docker-grader statements below are
> historical, describing the pre-#62 state.** Docker has since been removed from the
> project entirely. The CODE pipeline no longer grades blind diffs in per-task
> SWE-bench containers; instead it runs the **Claude Code CLI as a genuine coding
> agent** (real checkout / edit / test, with `git diff` captured) graded by
> `LocalExecGrader` (a local venv) for execution benchmarks or by retrieval metrics
> for ContextBench. The `SWEBenchDockerGrader` and the `swebench` extra are gone.
> See **ADR-eval-002** and **PR #62**.
>
> Consequently **PRD-8 ("real CODE scoring")** is **re-scoped**: the requirement is
> now satisfied via the CLI-coding-agent + `LocalExecGrader`/retrieval path rather
> than the official SWE-bench Docker harness. The PRD-8 finding below (and the
> related Gap §3.2) records the **pre-#62** state and should be read as historical.

---

## 1. Executive summary

**Verdict: substantially compliant on infrastructure and contracts; the central
product *hypothesis* is implemented but NOT yet demonstrated, and two planned
modules (the OpenCode integration and Scott B.'s dreaming *worker*) were
intentionally superseded or remain stubbed.**

The evaluation harness, frozen contract, all five benchmark loaders, all four
metrics, the cost/budget + sharded-key workflow, the three storage backends, the
rule-based + learned-classifier router, the (then-present) real SWE-bench Docker
grader (removed in #62; see the update note above), and a
shipping Claude Code plugin were all built and behaved as specified. The system was
deliberately re-platformed from the PRD/plan's **OpenCode** agent onto the
**Claude Code CLI** (documented in ADRs), which left the original `opencode/`
framework and the `dreaming/worker.py` consolidation engine as stubs — their
*intended functions* are now delivered (or partly delivered) by the plugin +
`dreaming/engine.py` Daydream path instead.

The PRD's frozen acceptance criterion — *Haiku + harness beats Opus-4.8-no-memory
on ≥2 of 5 benchmarks across the four metrics, < ~10% memory overhead* — is **not
met today**: the only runs on record (`results/v0.1/`) compare Haiku built-in vs.
Haiku plugin memory (not vs. Opus no-memory), the plugin currently **loses** to
Claude Code's built-in memory on both discriminative QA benchmarks, the three CODE
benchmarks score 0.00 (a harness limitation, not a memory signal), and efficiency
is not yet within budget. The repo is honest about this (`results/v0.1/README.md`).

**Status counts (28 requirements):**
**Done 16 · Partial 5 · Missing 1 · Deviated 4 · Descoped 2**

---

## 2. Compliance matrix

| Req id | Requirement (source) | Status | Evidence | Notes |
|---|---|---|---|---|
| PRD-1 | Pluggable, model-agnostic memory harness over public benchmarks | **Done** | [protocols.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/protocols.py#L25) · [harness.py `run`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/harness.py#L272) | Single `run()` drives all five benchmarks via injected `MemoryStore`/`ModelAdapter`. |
| PRD-2 | **Hypothesis met:** Haiku+harness beats Opus-4.8-no-memory on ≥2/5 benches, all 4 metrics, <~10% overhead | **Missing** | [results/v0.1/README.md](https://github.com/kenhuangus/agent-memory-harness/blob/main/results/v0.1/README.md#L55) | Acceptance criterion NOT demonstrated. Runs compare Haiku builtin vs plugin (not vs Opus); plugin **loses** on both QA benches; CODE = 0.00; efficiency out of budget. See §3. |
| PRD-3 | Four metrics map 1:1 to `metrics.py` (recency, efficiency, relevancy, accuracy) | **Done** | [metrics.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/metrics.py#L147) | All four implemented, deterministic, stdlib-only. Relevancy made scorer-agnostic (gold precision) — a justified refinement. |
| PRD-4 | Contract layer (`schema.py`+`protocols.py`) stdlib-only, Python 3.11+ | **Done** | [schema.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/schema.py#L26) · [protocols.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/protocols.py#L18) | No third-party imports at module scope; `from __future__` + stdlib only. |
| PRD-5 | Offline eval path reproducible with zero required dependencies | **Done** | [stores/__init__.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/stores/__init__.py#L12) · [metrics.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/metrics.py#L38) | Heavy deps lazy-imported; InMemoryStore + EchoModel reference stubs. |
| PRD-6 | Embedding default Voyage `voyage-3-large`, bge-m3 fallback, injected behind `MemoryStore` | **Partial** | [embedders.py `VoyageEmbedder`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/stores/embedders.py#L98) | VoyageEmbedder (1024-d) shipped + injectable via `SqliteVectorStore(embed=...)`. `bge-m3` fallback is documented but no concrete bge adapter ships — only Voyage + a Mock/hashing default. |
| PRD-7 | Reranker default Voyage `rerank-2.5` / Cohere `rerank-3.5`, rerank top ~50 | **Missing** | (no reranker module found) | No reranker adapter implemented anywhere in `stores/`. Spec'd in PRD §7.1 but absent. |
| PRD-8 | Real CODE scoring via official SWE-bench Docker harness (FAIL_TO_PASS ∧ PASS_TO_PASS), `sb-cli` cloud option, per-bench grader | **Partial** *(historical — re-scoped post-#62; see update note)* | [grader.py `SWEBenchDockerGrader`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/grader.py#L137) | *As of this audit (pre-#62):* the Docker grader implemented the exact SWE-bench resolved rule and was wired per-benchmark. But the agent emitted prose-or-empty diffs, so all CODE tasks graded `False`/None (0.00 on record); `sb-cli` cloud option not implemented. **(Docker grader removed in #62; CODE scoring re-scoped to the Claude Code CLI coding agent + `LocalExecGrader`/retrieval — see update note above.)** |
| PRD-9 | Live pricing table in `cost.py` (Haiku/Sonnet/Opus $/Mtok) | **Done** | [cost.py `PRICING`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/cost.py#L53) | Matches PRD §7.3 exactly; also adds OpenRouter subconscious-model prices. |
| PRD-10 | Default per-run budget $10, override `--budget-usd`, `<=0` disables | **Deviated** | [cost.py `DEFAULT_BUDGET_USD`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/cost.py#L44) | Default is **$200**, not the PRD's $10 (docstring explains: headroom for group-aware code-bench floors). Override + `<=0`-disable behavior preserved. Reasonable deviation; PRD text now stale. |
| PLAN-1 | Five benchmarks + one loader each, registered | **Done** | [loaders/__init__.py `_REGISTRY`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/loaders/__init__.py#L30) | All five (memoryagentbench, longmemeval, swe_contextbench, swe_bench_cl, contextbench) registered with concrete loaders present. |
| PLAN-2 | Four memory modes off/builtin/plugin/plugin-real | **Done** | [claudecode/agent.py `_MODES`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/agent.py#L42) | All four dispatched in `solve()`; plugin via MCP+priming, plugin-real installs the shipping plugin natively (black box). |
| PLAN-3 | Storage backend 1: Markdown + YAML, inverted keyword index | **Done** | [markdown_store.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/stores/markdown_store.py#L51) | OKF-backed, inverted token→{item_id} index, BM25 ranking, `as_of` honored. |
| PLAN-4 | Storage backend 2: SQLite + vectors, dense ANN (HNSW/FAISS) | **Deviated** | [sqlite_store.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/stores/sqlite_store.py#L1) | Implemented as SQLite rows + **brute-force cosine** over a char-n-gram hashing embedder (v1), not HNSW/FAISS. Documented as the v1; ANN is a "paid-path upgrade." Contract unchanged. |
| PLAN-5 | Storage backend 3: Graph store (Neo4j), typed traversal | **Deviated** | [graph_store.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/stores/graph_store.py#L1) | Implemented as a **stdlib in-memory** node/edge graph with BFS over OKF links (untyped, undirected v1), not Neo4j typed traversal. Documented as v1; Neo4j is the seam'd upgrade. |
| PLAN-6 | Intelligent router: rule-based → learned-classifier upgrade path | **Done** | [router.py `Router`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/router.py#L725) · [`SemanticRouterClassifier`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/router.py#L325) | Rule classifier ships; learned/semantic-exemplar classifier seam exists behind same `route()`; write-routing + dedup added. |
| PLAN-7 | Retrieval orchestrator: rank by recency × relevancy, dedup, return tight context | **Partial** | [router.py `route`/`write`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/router.py#L769) · [client.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/plugin/cookbook_memory/core/client.py#L59) | Routing + per-backend BM25 ranking + write-time dedup + cascade exist. A single unified orchestrator that explicitly ranks by `recency × relevancy` across backends and de-dups on read is not a discrete component; ranking is per-store BM25/score, not a recency×relevancy product. |
| PLAN-8 | Dreaming worker: dedup across backends, conflict resolution, governance (must-know/must-do/blacklist), retention+pruning | **Partial** | [dreaming/worker.py (stub)](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/dreaming/worker.py#L20) · [dreaming/engine.py `daydream`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/dreaming/engine.py#L67) | The planned 4-job `DreamingWorker` is a **NotImplementedError stub**. The Daydream `Stop`-hook engine (read logs → redact → LLM-extract → write, fail-open, incremental cursor) is fully built and is the de-facto consolidation path. Dedup lives in `router.write`; conflict-resolution / must-know-must-do / read-time retention+pruning are NOT implemented (only state-file TTL pruning exists). See §3 + §4. |
| PLAN-9 | Async dreaming runs automatically on session end (Stop hook) | **Done** | [engine.py module docstring](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/dreaming/engine.py#L1) · [hooks/hooks.json](https://github.com/kenhuangus/agent-memory-harness/blob/main/plugin/cookbook_memory/adapters/claude_code/hooks/hooks.json) | `daydream()` is the Stop-hook entrypoint; plugin registers the hook. Matches architecture.md §7.2. |
| PLAN-10 | OpenCode integration (wrap agent loop, write/read each step) | **Deviated/Descoped** | [opencode/framework.py (stub)](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/opencode/framework.py#L33) · [ADR-harness-001](https://github.com/kenhuangus/agent-memory-harness/blob/main/docs/adrs/ADR-harness-001-claude-code-plugin-shape.md) | `MemoryFramework` is an all-`NotImplementedError` stub. The project pivoted to the **Claude Code CLI** as the primary agent path (ADR-harness-001, ADR-eval-001). The OpenCode adapter remains as a documented future sibling. See §4. |
| PLAN-11 | Frozen contract: `MemoryStore`/`ModelAdapter`/`Loader` + data model + invariants | **Done** | [protocols.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/protocols.py#L25) · [schema.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/schema.py#L107) | All three protocols + all listed dataclasses present; invariants (descending score+rank, version counter, as-of, lazy heavy deps) encoded and documented. |
| PLAN-12 | Sharded-key workflow (per-captain keys/budgets, cheapest-first ordering) | **Done** | [cost.py `load_key_config`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/cost.py#L260) · [`cheapest_first`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/cost.py#L295) | Per-captain key config loader + Haiku+mem→Haiku→Sonnet→Opus ordering implemented. |
| PLAN-13 | Hard budget gates: per-run $ + token ceiling, abort + partial results | **Done** | [cost.py `CostTracker`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/cost.py#L137) | USD + token caps, `BudgetExceeded` carries partial-run numbers, `would_exceed` pre-check. |
| PLAN-14 | Cache + resume; checkpoint per task; incremental result saves | **Partial** | [run_bench.py `progress_cb`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L163) | Incremental per-task result rewrite + per-mode record files implemented. A content-hash call cache that avoids re-paying across re-runs is not evident in the claudecode path. |
| PLAN-15 | Group-aware / continual-learning ordering (SWE-Bench-CL, SWE-ContextBench) | **Done** | [run_bench.py `_GROUP_AWARE`](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L60) | Group-aware draw + per-benchmark long-memory floors tuned to dataset structure. |
| PLAN-16 | Versioned per-benchmark result files `results/{vX.Y}/{bench}-{ts}.json` | **Done** | [run_bench.py results wiring](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/claudecode/run_bench.py#L312) · [results/v0.1/](https://github.com/kenhuangus/agent-memory-harness/blob/main/results/v0.1) | Matches architecture.md §7.1; real result files present for v0.1 and v0.1-bm25. |
| PLAN-17 | Results dashboard / scoreboard on the project site | **Done** | [results.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/eval/memeval/results.py#L1) · [results.html](https://github.com/kenhuangus/agent-memory-harness/blob/main/results.html) | Ledger → `results.json` → `results.html` fetch/render pipeline; full site (index/plan/architecture/benchmarks/results/collaborate) present. |
| PLAN-18 | Memory mechanism shipped as an installable Claude Code plugin (skills · MCP · hooks) | **Done** | [plugin/cookbook_memory/](https://github.com/kenhuangus/agent-memory-harness/blob/main/plugin/cookbook_memory/core/client.py#L1) · [contract.py](https://github.com/kenhuangus/agent-memory-harness/blob/main/plugin/cookbook_memory/core/contract.py#L1) | Plugin bundles MCP server + hooks + recall skill; single import seam to the engine (Router+stores). Recall-only conscious surface (ADR-harness-008). |

---

## 3. Gaps & risks (Missing / Partial that matter)

1. **PRD-2 — the headline hypothesis is unproven (Missing).** The frozen
   acceptance criterion (Haiku+harness > Opus-4.8-no-memory on ≥2/5 benches,
   all four metrics, <~10% overhead) has no supporting run. What exists:
   Haiku **built-in** vs Haiku **plugin** on two QA benches, where the plugin
   *loses* (LongMemEval 0.20 vs 0.35; MemoryAgentBench 0.40 vs 0.75), the three
   CODE benches at 0.00, and efficiency anomalously > 1 (e.g. 31.9). *To close:*
   run the actual PRD grid including an Opus-4.8-no-memory baseline; fix the
   efficiency computation so it is a true ratio; land turn-level chunking to lift
   QA accuracy (already identified in `results/v0.1/README.md` and `suggestion.md`).

2. **PRD-8 / CODE benches carry no memory signal (Partial).** *(Pre-#62 finding —
   the Docker grader described here was removed in #62; see the update note above.)*
   At audit time the Docker grader was correct, but the agent emitted prose/empty
   patches and CODE tasks bypassed the memory loop entirely, so SWE-Bench-CL /
   SWE-ContextBench / ContextBench were structurally 0.00. *To close (now addressed
   in #62):* make the CODE agent emit applyable diffs and route CODE retrieval
   through memory (or explicitly scope memory to QA and amend the plan's "memory on
   CODE" claim). Post-#62 the CODE agent is the Claude Code CLI itself (real
   checkout/edit/test, `git diff` captured) graded by `LocalExecGrader`/retrieval.

3. **PLAN-8 — planned dreaming consolidation jobs missing (Partial).** Conflict
   resolution, must-know/must-do/blacklist governance, and read-time
   retention+pruning are not implemented (`worker.py` is a stub). Only write-time
   dedup (router) + Daydream extraction + state-file TTL pruning exist. *To close
   (per the "team-owned, don't change" constraint — flag, don't fix):* either
   implement the four jobs in the engine path or formally descope them via an ADR.

4. **PRD-7 — no reranker (Missing).** PRD §7.1 commits to a Voyage/Cohere
   reranker over the top ~50 ANN hits; none ships. *To close:* add a reranker
   adapter behind the store seam, or descope for v1.

5. **PRD-6 / PLAN-14 — partial (bge-m3 fallback adapter; content-hash cache).**
   The open-source embedding fallback and the cross-run call cache are documented
   intentions without concrete implementations in the audited paths.

---

## 4. Deviations (built differently than planned — and whether that's reasonable)

1. **OpenCode → Claude Code CLI pivot (reasonable, documented).** The PRD/plan
   name OpenCode as the agent integration; the project re-platformed onto the
   Claude Code CLI as the primary path, leaving `eval/memeval/opencode/` as a
   stubbed future-sibling adapter. This is captured in
   [ADR-harness-001](https://github.com/kenhuangus/agent-memory-harness/blob/main/docs/adrs/ADR-harness-001-claude-code-plugin-shape.md)
   and [ADR-eval-001](https://github.com/kenhuangus/agent-memory-harness/blob/main/docs/adrs/ADR-eval-001-extract-memory-package.md),
   and `architecture.md` §7 reconciles it. The Claude Code path gives a real,
   installable plugin and a subscription-auth (no-API-billing) eval — a sound
   trade. **Reasonable.** The residual risk is doc drift: PRD §5/§7 and
   `project-plan.md` still speak in OpenCode terms.

2. **Dreaming split: planned `DreamingWorker` (4 jobs) → `daydream()` engine
   (extract-on-Stop) (reasonable but incomplete).** The async-consolidation idea
   landed as the Daydream Stop-hook pipeline rather than the plan's batch
   consolidation worker. The new shape (incremental cursor, redaction, fail-open,
   OpenRouter cheap model) is well-engineered and matches `architecture.md` §7.3.
   **Reasonable as a redesign**, but it does not yet deliver conflict resolution /
   governance / retention (see Gap 3) — so it is a deviation *and* a partial.

3. **SQLite vector backend: HNSW/FAISS → brute-force cosine over char-n-gram
   hashing (reasonable v1).** Keeps the zero-dependency offline guarantee; the
   real dense embedder + ANN index are injectable upgrades behind an unchanged
   `MemoryStore` contract. **Reasonable** for the sprint; flagged so nobody reads
   offline similarity numbers as true dense semantics.

4. **Graph backend: Neo4j typed traversal → stdlib in-memory BFS over OKF links,
   untyped/undirected (reasonable v1).** Same rationale — preserves offline
   purity; Neo4j is the seam'd paid-path upgrade. **Reasonable** for v1.

**Descoped (intentional, consistent with PRD non-goals):** production hardening,
multi-tenant/auth/SLAs/distributed deployment (PRD §3) — correctly absent. Building
a home-grown benchmark/dataset is also correctly avoided (all five are public).

---

*Scope note:* this audit is read-only over code + docs at `ebf6e2f`. Where a
behavior could not be exercised live (e.g. Docker SWE-bench grading, Voyage API
calls, a full PRD eval grid), the verdict rests on code inspection and the repo's
own recorded results; such items are marked Partial/Missing rather than asserted
Done. The memory mechanism (stores/router/okf/dreaming engine) is assessed against
the plan but, per instruction, not proposed for change.
