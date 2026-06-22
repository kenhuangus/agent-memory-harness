# Analysis & Ablation Study - Literature Survey for the Memory Harness

**Status:** docs-only research note. No code changes implied.
**Date:** drafted 2026-06-22 on docs/analysis-ablation-survey.
**Purpose:** Map how the papers behind our five benchmarks (plus a few influential
memory-agent papers) structure their Analysis and Ablation Study sections, then
synthesize a concrete Analysis & Ablation outline for OUR harness, grounded in
our 4 modes x 4 metrics x 5 benchmarks setup (see eval/memeval/metrics.py,
results/v0.1/README.md, suggestion.md).

The goal is prescriptive: which ablations should we run, which slices should we
report, which tables/figures, and how each maps onto recency / efficiency /
relevancy / accuracy across off / builtin / plugin / plugin-real.

---

## 1. Executive summary - recommended outline for OUR harness

We have a small number of well-defined knobs (mode, scorer, top-k, chunking
granularity, dedup-on-write, model tier, memory on/off). The survey converges on
a stable shape for an analysis section that we should adopt almost verbatim:

1. Headline per-benchmark table - 4 modes x 4 metrics x 5 benchmarks, with
   the memory-off cell highlighted as the baseline lift target. This is the
   LongMemEval Table 2 / overall pattern and the MemoryAgentBench Table 2 /
   competency pattern.
2. Slice tables - break accuracy down by question category on LongMemEval
   (5 abilities), by competency on MemoryAgentBench (AR / TTL / LRU / SF),
   by retrieval level (file / block / line) on ContextBench, by experience
   condition on SWE-ContextBench (no / oracle / free x full / summary),
   and by sequence position + FWT/BWT on SWE-Bench-CL.
3. Ablation studies - one figure or small table per knob; each holds all
   other knobs fixed at our reference plugin configuration and varies one
   axis. Every ablation reports the same 4 metrics so the trade-offs (e.g.
   recency up but efficiency down) are visible.
4. Error taxonomy - adopt the LongMemEval / suggestion.md four-class
   breakdown (recall miss / answer-not-in-content / gold-retrieved-but-wrong
   / grading error) as a small stacked-bar figure per mode.
5. Cost/efficiency analysis - token overhead, wall-clock, and dollar per task,
   mirroring SWE-ContextBench sec 3.4-3.5 and LoCoMo RAG-k curves.

The ablations we should run, in priority order (mapped to our knobs):

| # | Ablation | Vary | Hold fixed | Primary metrics to read | Inspired by |
|---|---|---|---|---|---|
| 1 | Chunk granularity | whole-session vs per-turn (and 1-3 turn windows) | mode=plugin, scorer=BM25, k=5, Haiku | accuracy, relevancy, efficiency | LongMemEval sec 5.2 (Fig 5), MemoryAgentBench sec 4.3.1 (Fig 2) |
| 2 | Retrieval scorer | Jaccard vs BM25 vs (optional) embedding | mode=plugin, chunk=session, k=5, Haiku | relevancy (already scorer-agnostic), recency, accuracy | MemoryAgentBench Tab 3 backbone, LongMemEval sec E.2 retriever |
| 3 | Top-k | k in {2, 5, 10} | mode=plugin, BM25, chunk=session, Haiku | accuracy, efficiency, relevancy | MemoryAgentBench sec 4.3.2 (Fig 3), LoCoMo top-5/25/50 |
| 4 | Memory mode | off / builtin / plugin / plugin-real | benchmark, Haiku, k=5 | accuracy + accuracy_lift, efficiency | LongMemEval commercial-systems study (App. B) |
| 5 | Model tier x memory | Haiku / Sonnet / Opus x {memory off, on} | mode=plugin or off, k=5 | accuracy (frontier-no-mem <= Haiku+mem hypothesis) | MemoryAgentBench sec 4.3.3 (Tab 3), our project hypothesis |
| 6 | Recall-query shape | full-question vs grep-style literal-token query | mode=plugin, BM25, chunk=turn | relevancy, recency, accuracy | LongMemEval sec 5.4 time-aware query expansion |
| 7 | Dedup-on-write | on / off | mode=plugin, BM25, k=5 | efficiency, accuracy | Mem0 update-module ablation |
| 8 | Recency / temporal weighting | none / decay-only / time-filter (where available) | mode=plugin, BM25, k=5 | recency, recency_decayed, accuracy on LongMemEval temporal subset | LongMemEval sec 5.4 (Tab 4) |

Each row above is a single experiment. Together they cover every lever we
actually have. None of them require changes to the memory mechanism itself -
they are runs of the existing harness with different flags / store
configurations, which is exactly what an ablation section is supposed to be.

---

## 2. Cross-paper comparison table

| Paper | Analysis slices | Ablation axes | Key metrics | Error / qualitative |
|---|---|---|---|---|
| LongMemEval (2410.10813) | 5 question types (IE, MR, KU, TR, ABS); model size; dataset scale (S/M/Oracle); commercial system (ChatGPT / Coze) | Value granularity (session/round/round+summary/round+facts); key expansion (K=V, +facts, +summary, +keyphrase); time-aware query expansion; reading strategy (CoN, JSON); retriever; rank merging | Recall@k, NDCG@k, QA accuracy (LLM-judge w/ 97 pct human agreement) | App. E.5 error patterns; ChatGPT overwrites, Coze fails to record |
| MemoryAgentBench (2507.05257) | 4 competencies (AR, TTL, LRU, SF); task subtype (EventQA, FactConsolidation SH/MH); context length (6K/32K/64K/262K); model | Chunk size (Fig 2); top-k in {2,5,10} (Fig 3); backbone model x 4 agents (Tab 3); FactConsolidation context-length validation (Tab 4) | SubEM, GPT-4o-judge, F1, Recall@5, MCC accuracy | Per-category strengths/weaknesses; SF multi-hop <= 7 pct across all; long-context degradation |
| ContextBench (2602.05892) | Per repo / per language (66 repos, 8 langs); per agent (5 scaffolds); per model (4 LLMs); difficulty (Lite-500 subset); recall-vs-precision; explored vs utilized | Agent framework x fixed model (RQ1); model x fixed scaffold (RQ2); retrieval-pattern dissection (RQ3); efficiency/redundancy/usage-drop (RQ4); gold-context robustness w/ alt. patches (RQ5) | Context recall/precision/F1 at file/block/line; efficiency, redundancy, usage drop; Pass@1 | App. I three case studies (Prometheus, Agentless, OpenHands); App. K Devstral contamination |
| SWE-ContextBench (2602.08316) | Per repo (12 repos); related-task category (6 groups); difficulty proxy by baseline runtime; test split (F2P vs P2P) | 5 experience-reuse settings: {No-Exp, Free Exp, Oracle Exp, Free Summary, Oracle Summary} | F2P / P2P / task resolution rate; runtime (s); cost (dollars); cache-token share | Qualitative on summary-vs-trajectory; high-variance Free-Exp runs |
| SWE-Bench-CL (2507.00014) | Per-repo sequences; sequence position; semantic task similarity; FWT / BWT / forgetting curves | Semantic memory on/off; FAISS variants; memory buffer size; model | Avg accuracy; Forward Transfer; Backward Transfer; Retention | Failure patterns by domain; degradation trajectories |
| LoCoMo (2402.17753) | 5 QA categories (single-hop, multi-hop, temporal, open-domain, adversarial); model; conv length (Fig 4B) | Long-context window in {4K, 8K, 12K, 16K}; RAG unit (dialog / observation / summary) x top-k; MM-dialog training variants | F1, Recall@k, ROUGE-1/2/L, FactScore, MM-Relevance | 5-type error taxonomy (missing info, hallucination, cue misunderstanding, speaker attribution, salience misjudgment); App. D.1 Tab 7 |
| MemGPT (2310.08560) | Document QA (single/multi-doc), MSC, nested KV retrieval (depth & breadth) | Memory-hierarchy levels; main-context vs paging into external; function-call schema variants | Doc-QA accuracy, MSC accuracy, nested-KV solve rate | Failures at recursion depth |
| A-MEM (2502.12110) | LoCoMo categories; 6 foundation models | Note-construction off; link-generation off; memory-evolution off | LoCoMo F1 / Judge | (Limited detail in abstract; verify in full paper before citing) |
| Mem0 (2504.19413) | LoCoMo 4 categories; base vs graph variant | Extraction off; update/consolidation off; retrieval-policy variants; graph vs base | F1, BLEU, LLM-judge, token cost, p95 latency | 26 pct rel. judge gain vs OpenAI baseline; 90 pct token, 91 pct p95 reduction |

---

## 3. Per-paper extraction (with URLs)

For each paper: (1) analysis slices, (2) ablations, (3) metrics, (4) error /
qualitative. Numbers are paraphrased from the paper text; verify before
citing in a publication.

### 3.1 LongMemEval - 2410.10813
URL: https://arxiv.org/abs/2410.10813 - HTML: https://arxiv.org/html/2410.10813

1. Analysis slices: 5 memory abilities (IE / MR / KU / TR / ABS; ABS via
   30 false-premise questions); models (GPT-4o, Llama 3.1 70B/8B, Phi-3 14B,
   Phi-3.5 Mini 4B, plus 5 more in App. E.1); dataset scale (S ~ 115K tok/q,
   M ~ 1.5M tok/q, Oracle); commercial systems (ChatGPT, Coze on a 97-q
   subset, 3-6 sessions); error patterns (App. E.5).
2. Ablations:
   - Value granularity (sec 5.2, Fig 5): {entire session, rounds,
     rounds+summary, rounds+facts}. Decomposing into rounds significantly
     helps GPT-4o reading; fact extraction helps only multi-session reasoning.
   - Key/index expansion (sec 5.3, Tab 3): {K=V, K=fact, K=keyphrase,
     K=summary, K=V+fact, K=V+summary, K=V+keyphrase} on both round and
     session values. K=V+fact: +9.4 pct recall@k, +5.4 pct accuracy.
   - Time-aware query expansion (sec 5.4, Tab 4): baseline vs +query
     expansion with M_T in {GPT-4o, Llama 8B}; +11.3 pct recall on rounds.
   - Reading strategy (sec 5.5, Fig 6): {NL baseline, Chain-of-Note (CoN),
     JSON, CoN+JSON} under oracle retrieval. Up to 10-pt absolute spread
     even with perfect recall. CoN+JSON consistently best.
   - Retriever (sec E.2): Stella V5 1.5B vs alternatives.
   - Rank merging (sec E.3): post-retrieval merge underperforms index-stage
     concatenation.
   - Time-extractor strength (sec E.4): GPT-4o vs Llama 8B for time-range
     extraction.
3. Metrics: QA accuracy via gpt-4o-2024-08-06 judge (>97 pct human
   agreement); Recall@k; NDCG@k; performance-drop pct vs Oracle.
4. Error / qualitative: App. B - ChatGPT overwrites prior facts as chat
   continues; Coze fails to record indirectly provided user info.

### 3.2 MemoryAgentBench - 2507.05257
URL: https://arxiv.org/abs/2507.05257 - HTML: https://arxiv.org/html/2507.05257v2

1. Analysis slices: 4 competencies (AR / TTL / LRU / SF); task subtype
   (EventQA on 5 books >390K tok, 6-way MCQ; FactConsolidation SH/MH at 6K /
   32K / 64K / 262K); context length sweeps; models (GPT-4o, GPT-4o-mini,
   GPT-4.1-mini, Gemini 2.0 Flash, Claude 3.7 Sonnet).
2. Ablations:
   - Chunk size (sec 4.3.1, Fig 2): smaller chunks improve AR but hurt
     LRU. AR uses 512 tok, others 4096; commercial agents (Mem0, Cognee,
     Zep, MIRIX) standardized at 4096.
   - Top-k (sec 4.3.2, Fig 3): k in {2, 5, 10}; larger k generally helps
     subject to a 40K-tok input cap (Tab 9 in App. D.3).
   - Backbone model (sec 4.3.3, Tab 3): GPT-4o-mini -> GPT-4.1-mini ->
     Gemini-2.0-Flash across BM25 / Text-Embed-3-Small / GraphRAG / MIRIX.
     RAG agents gain ~0.4-1.9 pts; agentic memory agents gain 9.7 pts (MIRIX
     overall), 23.2 pts on EventQA. Headline: model upgrades barely help
     RAG; they substantially help agentic memory.
   - Dataset validation (sec 4.3.4, Tab 4): FactConsolidation at 6K shows
     the task is solvable (92-100 pct SH); 32K drops it to 14-88 pct,
     isolating a long-context reasoning failure rather than a data issue.
3. Metrics: SubEM (AR doc-QA), GPT-4o-judge (LongMemEval), accuracy
   (EventQA, classification, detective QA), Recall@5 (recommendation),
   F1 (summarization).
4. Error / qualitative: SF multi-hop <= 7 pct across all methods - a
   universal failure mode. Long-context degradation: 6K -> 32K SH costs
   4-31 pts, MH costs 14-66 pts.

### 3.3 ContextBench - 2602.05892
URL: https://arxiv.org/abs/2602.05892 - HTML: https://arxiv.org/html/2602.05892v3

1. Analysis slices: 66 repos x 8 langs (Tab 1); 5 agent scaffolds
   (mini-SWE-Agent baseline, Agentless, SWE-Agent, OpenHands, Prometheus -
   Tab 2 holding GPT-5 fixed); 4 frontier models (GPT-5, Claude Sonnet 4.5,
   Gemini 2.5 Pro, Devstral 2 - Tab 3 holding scaffold fixed); difficulty
   via {solvability, edit scope, edit dispersion}; 500-task Lite subset;
   recall-vs-precision trade-off (block F1 < 0.45); explored-vs-utilized
   (Tab 5: efficiency / redundancy / usage-drop).
2. Ablations: RQ1 scaffold-only, RQ2 model-only, RQ3 retrieval-pattern
   (steps x lines x cost), RQ4 scaffold-component dynamics, RQ5
   gold-context robustness against alternative patches (Jaccard 0.9518
   over 82 tasks).
3. Metrics: context recall / precision / F1 at file / block / line;
   efficiency (early gold capture); redundancy (overlap); usage drop
   (explored -> utilized); Pass@1.
4. Error / qualitative: App. I three case studies - Prometheus
   incomplete class-semantics, Agentless file-localization failure,
   OpenHands cross-context consolidation gap. App. K Devstral contamination
   / format-misalignment caveat.

### 3.4 SWE-ContextBench - 2602.08316
URL: https://arxiv.org/abs/2602.08316 - HTML: https://arxiv.org/html/2602.08316v1

Note: this paper does not have a labelled Ablation Study section; sec 3 is
structured as 5 experience-reuse settings and post-hoc analysis by
accuracy / time / cost.

1. Analysis slices: per-repo (12 repos; Django 114/36, SymPy 77/26
   dominant); related-task category (6 groups: multi-issue, PR<->issue,
   etc.); difficulty by baseline runtime; F2P (avg 5.09) vs P2P
   (avg 128.32) tests.
2. Ablations: {No-Experience, Free Experience, Oracle Experience, Free
   Summary, Oracle Summary} - orthogonal in two dimensions (full trajectory
   vs ~204-word summary; oracle retrieval vs autonomous).
3. Metrics: F2P pass rate (19.84-27.48 pct), P2P pass rate (97-99 pct),
   task-level resolution (22.22 pct Free-Sum vs 34.34 pct Oracle-Sum vs
   26.26 pct baseline); runtime (356.95 s Oracle-Sum vs 406.77 s Free-Exp);
   cost (0.77 dollars vs 0.98 dollars); cache tokens >97 pct of usage.
4. Error / qualitative: Oracle Summary beats Oracle Experience -
   concise summaries beat raw traces. Free Summary underperforms baseline -
   wrong summaries actively mislead. Free Experience has highest variance
   (max runtime >2,100 s).

### 3.5 SWE-Bench-CL - 2507.00014
URL: https://arxiv.org/abs/2507.00014 - PDF: https://arxiv.org/pdf/2507.00014

1. Analysis slices: per-repo problem sequences; sequence-position
   effects; semantic task similarity; FWT, BWT, catastrophic forgetting.
2. Ablations: semantic-memory on/off; FAISS configuration; memory
   buffer size; LLM choice.
3. Metrics: average accuracy; Forward Transfer (FWT); Backward Transfer
   (BWT); Retention.
4. Error / qualitative: failure patterns by domain; degradation
   trajectories; semantic-cluster effects. (Detail compressed in PDF; the
   web-fetch summary above is best-effort - verify against the PDF before
   citing specific numbers.)

### 3.6 LoCoMo - 2402.17753
URL: https://arxiv.org/abs/2402.17753 - HTML: https://arxiv.org/html/2402.17753v1

1. Analysis slices: 5 QA categories (single-hop 29.9 -> 56.4 pct,
   multi-hop 23.3 -> 42.0 pct, temporal 17.5 -> 25.0 pct, open-domain,
   adversarial 12.8 -> 2.1 pct); model (Mistral-7B, Llama-70B, GPT-3.5,
   GPT-4); conv length (Fig 4B - MM-Relevance drops with length).
2. Ablations:
   - Long-context window in {4K, 8K, 12K, 16K} on GPT-3.5-turbo-16K.
     Overall F1 31.7 -> 56.4 pct but adversarial collapses 70.2 -> 2.1 pct.
   - RAG retrieval unit in {dialog, observation, summary} x top-k.
     Dialog top-5/25/50: 31.7/35.8/34.8 F1, 58.8/79.9/84.8 recall@k.
     Observation top-5: 41.4 F1 (best, but recall@5 only 49.6 pct).
     Summary top-2: 29.9 F1; top-10: 31.5 F1, recall@10 90.7 pct.
     Conclusion: signal-to-noise dominates.
   - MM dialog training: {base, +summary, +observation}; observation wins.
3. Metrics: F1 (normalized exact-match w/ paraphrase tolerance);
   Recall@k; ROUGE-1/2/L; FactScore (P/R/F1 via atomic-fact decomposition);
   MM-Relevance.
4. Error / qualitative (Tab 7, App. D.1): five categories - (1)
   missing info / failure to make temporal-causal links, (2) hallucination,
   (3) cue misunderstanding (humor/sarcasm), (4) speaker-attribution,
   (5) salience misjudgment. Human F1 = 87.9 pct (ground-truth gap).

### 3.7 MemGPT - 2310.08560
URL: https://arxiv.org/abs/2310.08560

Analysis on Document QA (single/multi-doc), MSC (multi-session chat), and
nested-KV retrieval (depth x breadth). Ablations toggle memory-hierarchy
levels (main vs external paging), function-call schemas, and recursion
limits. Concrete numbers were not extractable from the abstract alone; the
ablation tables live in the full paper PDF.

### 3.8 A-MEM - 2502.12110
URL: https://arxiv.org/abs/2502.12110

LoCoMo evaluation across 6 foundation models. Component ablations on
note-construction, link-generation, and memory-evolution. Specific numbers
not extractable from the abstract; verify against the paper before citing.

### 3.9 Mem0 - 2504.19413
URL: https://arxiv.org/abs/2504.19413

LoCoMo 4 categories. Ablations on extraction, consolidation/update,
retrieval policy, and graph variant. Headline metrics: 26 pct relative
LLM-judge gain over OpenAI baseline; graph variant ~+2 pts over base;
90 pct token cost reduction and 91 pct p95 latency reduction vs
full-context.

---

## 4. Synthesized Analysis & Ablation outline for OUR harness

Two governing principles from the survey:

- Show the lift, not just the level. Every paper that has a baseline (no
  memory, full context, or free condition) leads with delta vs that baseline.
  We have Metrics.accuracy_lift = accuracy - accuracy_memory_off for
  exactly this - it should be a column in every table.
- One axis per ablation. All the surveyed ablations vary a single knob
  with everything else fixed at a clearly-named reference configuration. We
  will define plugin-at-ref = {mode=plugin, scorer=BM25, chunk=whole-session,
  k=5, dedup=on, model=claude-haiku-4-5} and ablate around it.

### 4.1 Analysis section (per benchmark)

For each of the 5 benchmarks (LongMemEval, MemoryAgentBench, ContextBench,
SWE-ContextBench, SWE-Bench-CL), report:

A1. Headline table - 4 modes x 4 metrics + delta accuracy vs off,
plus n tasks and partial/budget flags. (Maps to the RunResult we already
log.)

A2. Per-category accuracy slice - paper-specific:

| Benchmark | Slice on | Source pattern |
|---|---|---|
| LongMemEval | 5 abilities (IE / MR / KU / TR / ABS) + n per cell | LongMemEval Tab 2 |
| MemoryAgentBench | 4 competencies (AR / TTL / LRU / SF) + EventQA / FactConsolidation breakouts | MAB Tab 2 / Tab 4 |
| ContextBench | File / block / line recall + precision + F1; explored-vs-utilized | ContextBench Tab 3-5 |
| SWE-ContextBench | Per related-task group + per repo (top 5 repos); F2P vs P2P pass rate; cost & runtime per task | SWE-ContextBench sec 3 |
| SWE-Bench-CL | Per-repo sequences; FWT, BWT, retention curve over sequence position | SWE-Bench-CL sec 3 |

A3. Retrieval-quality breakdown - for the QA benches, plot relevancy
(scorer-agnostic gold precision per metrics.py lines 261-330) and recency
vs accuracy as a scatter. This directly tests the suggestion.md claim
that recall is no longer the bottleneck.

A4. Error taxonomy - 4 classes from suggestion.md:
{recall miss, answer-not-in-content, gold-retrieved-but-wrong, grading
error}, plus a 5th adopted from LoCoMo (negation/cue/speaker error) where
applicable. Render as stacked bars per mode.

A5. Cost & efficiency - total tokens, memory tokens, wall-clock,
dollars per task. Hold model fixed at Haiku; report efficiency (already in
metrics) plus raw absolute numbers.

### 4.2 Ablation section (the 8 from sec 1, with details)

Each ablation is one experiment, one row of a small table, one figure
where a trend is informative. Reference config plugin-at-ref defined above.

Ablation 1 - Chunk granularity (highest priority per suggestion.md)
- Vary: {whole-session, per-turn, sliding-3-turn}.
- Benchmarks: LongMemEval (S), MemoryAgentBench (AR subset: SH-Doc-QA,
  MH-Doc-QA, LME). These two suffice because the surveyed papers
  (LongMemEval sec 5.2; MAB sec 4.3.1) show this is the dominant
  chunking-effect signal.
- Report: delta accuracy, relevancy, efficiency, recency vs the
  whole-session baseline. Expected sign per literature: accuracy up,
  relevancy up, recency up, efficiency slightly down (more tokens per query
  if k held constant; mitigated by smaller items).
- Figure: small-multiples (2 benches x 4 metrics), one bar per granularity.
- Mode: plugin (and re-run on plugin-real to confirm parity).

Ablation 2 - Retrieval scorer
- Vary: {Jaccard, BM25, embedding} (embedding optional if extras
  installed).
- Benchmark: LongMemEval-S, MemoryAgentBench AR.
- Report: relevancy (scorer-agnostic gold precision is the right metric
  here - that is exactly why we made it scorer-agnostic, see metrics.py
  lines 280-298), recency, accuracy.
- Note: results/v0.1-bm25 already documents the Jaccard -> BM25 step
  (relevancy 0.005 -> 0.57, recency 0.75 -> 0.84, accuracy 0.20 -> 0.25).
  The ablation formalizes that one-off into a proper table.

Ablation 3 - Top-k
- Vary: k in {2, 5, 10} (mirror MAB sec 4.3.2).
- Report: accuracy, efficiency, relevancy. Expect an
  accuracy/efficiency Pareto frontier; pick a reference k from the knee.
- Benchmark: all 3 QA benches (LME, MAB, LoCoMo if added).

Ablation 4 - Memory mode (the headline plot)
- Vary: off / builtin / plugin / plugin-real (all 4 modes).
- Benchmarks: all 5.
- Report: 4-metric table per benchmark. This IS the headline
  result; per README.md lines 96-109 we already run this.
- Add a commercial-systems-style commentary borrowed from LongMemEval
  App. B: where does each mode fail (overwrite? fail to record? bury in
  noise?).

Ablation 5 - Model tier x memory
- Vary: {Haiku, Sonnet, Opus} x {off, plugin} = 6 cells.
- Benchmark: LongMemEval-S + MemoryAgentBench (covers the project
  hypothesis without the full SWE-bench cost).
- Report: accuracy and accuracy_lift. The project hypothesis is
  accuracy(Haiku + plugin) >= accuracy(Opus + off); this ablation is
  the literal test of it. cheapest_first and should_early_exit in
  harness.py make this cost-controlled.

Ablation 6 - Recall-query shape
- Vary: {full-question-string, literal-token-extract} (the
  suggestion.md grep-style idea).
- Benchmark: LongMemEval-S.
- Report: relevancy, recency, accuracy. Compounds with Ablation 1
  (turn-level chunks) - could optionally run the 2x2.

Ablation 7 - Dedup-on-write
- Vary: {dedup on, dedup off}.
- Benchmark: LongMemEval-S, MAB AR.
- Report: efficiency, accuracy. Inspired by Mem0 update-module
  ablation; quantifies the cost of NOT consolidating.

Ablation 8 - Recency / temporal weighting
- Vary: {none, exp-decay (TAU_DEFAULT), hard time-filter where
  available}.
- Benchmark: LongMemEval temporal-reasoning subset (TR). This is the
  only benchmark in our set where temporal labels are first-class.
- Report: recency, recency_decayed, accuracy on TR only.
  Mirrors LongMemEval sec 5.4 Tab 4.

### 4.3 Tables / figures we should produce

- T1 Headline 4 x 4 x 5 table (modes x metrics x benchmarks).
- T2 Per-category accuracy (5 sub-tables, one per benchmark; see A2).
- T3 Each of the 8 ablations as its own small table.
- F1 Stacked error-class bars per mode per benchmark (A4).
- F2 Relevancy-vs-accuracy scatter, one point per task, colored by
  mode (A3). Tests the ranking-is-not-the-bottleneck claim.
- F3 Top-k Pareto: accuracy on the y, efficiency on the x, one
  curve per scorer (combines Ablations 2 and 3).
- F4 Chunk-granularity small-multiples (A4.2 / Ablation 1).
- F5 Cost dashboard: dollars/task and tokens/task per mode (A5).

Each chart should be reproducible from results/*/results.json plus
runs/*/trajectory.jsonl - no new logging is required.

---

## 5. Conventions that do NOT fit us, and why

- Long-context-window sweep (LoCoMo Tab. 4K/8K/12K/16K). We do not
  control the model context-window; it is whatever the Claude tier
  exposes. Skip - replaced by Ablation 5 (model tier).
- Backbone-model x multiple-agent-systems matrix (MAB Tab 3). We have
  one agent (Claude Code) with four memory modes, not five agent
  scaffolds. The analog is our Ablation 4 (mode) x Ablation 5 (model);
  fully crossing them would be 4 x 3 = 12 cells, which is fine for the
  small benches but too expensive for SWE-bench. Run the cross only on
  LME and MAB.
- CoN / JSON reading-strategy ablation (LongMemEval sec 5.5). We do not
  expose a reading-strategy knob inside Claude Code - it is whatever the
  CLI prompt and the model produce. Document as out-of-scope.
- FWT/BWT continual-learning metrics (SWE-Bench-CL). Sequence-aware
  metrics only make sense if we run SWE-Bench-CL in chronological order
  and reset memory at sequence boundaries. We should report them ONLY on
  SWE-Bench-CL; do not try to back-fit them onto the other 4 benches.
- Long-context-vs-RAG dichotomy (LoCoMo, MemGPT). Both modes in our
  setup ARE RAG-ish (builtin grep, plugin BM25-retrieval). We are not
  testing whether memory beats a giant context window; we are testing
  whether OUR memory beats Claude Code memory. The framing is
  different even when the table shape is similar.
- Frontier model count (ContextBench Tab 3 with 4 LLMs). We are tied to
  Anthropic models for the agent; cross-vendor model ablations do not
  apply.

---

## 6. Caveats and verification status

- arXiv IDs verified for all five benchmark papers via WebSearch
  (paper titles, authors, publication venue confirmed). The 2602.* IDs
  (ContextBench, SWE-ContextBench) are 2026 papers - they postdate the
  model training cutoff and were treated strictly as fetched-from-the-web
  evidence, not recalled.
- Numbers from sec 3.5 (SWE-Bench-CL) were extracted from a partially
  compressed PDF render and should be re-verified against
  https://arxiv.org/pdf/2507.00014 before citation.
- MemGPT / A-MEM details in sec 3.7-3.8 were extracted from abstracts
  only; the WebFetch on the full text was light. Confirm specific
  ablation tables against the full PDFs before publication. The
  ablation categories (memory-hierarchy levels for MemGPT;
  note/link/evolution for A-MEM) are stated in their abstracts and are
  safe to cite at that level of granularity.
- No code was modified in preparing this document; only this file
  was written, per the request scope.

---

## 7. Sources

Benchmark papers (the five we use):

- LongMemEval - https://arxiv.org/abs/2410.10813, HTML https://arxiv.org/html/2410.10813, site https://xiaowu0162.github.io/long-mem-eval/
- MemoryAgentBench - https://arxiv.org/abs/2507.05257, HTML https://arxiv.org/html/2507.05257v2, dataset https://huggingface.co/datasets/ai-hyz/MemoryAgentBench
- ContextBench - https://arxiv.org/abs/2602.05892, HTML https://arxiv.org/html/2602.05892v3, code https://github.com/EuniAI/ContextBench
- SWE-ContextBench - https://arxiv.org/abs/2602.08316, HTML https://arxiv.org/html/2602.08316v1, dataset https://huggingface.co/datasets/jiayuanz3/SWEContextBench
- SWE-Bench-CL - https://arxiv.org/abs/2507.00014, PDF https://arxiv.org/pdf/2507.00014, code https://github.com/thomasjoshi/agents-never-forget

Complementary / influential memory-agent papers:

- LoCoMo - https://arxiv.org/abs/2402.17753, HTML https://arxiv.org/html/2402.17753v1, site https://snap-research.github.io/locomo/
- MemGPT - https://arxiv.org/abs/2310.08560
- A-MEM - https://arxiv.org/abs/2502.12110
- Mem0 - https://arxiv.org/abs/2504.19413

Internal context references:

- README.md
- eval/README.md
- eval/memeval/claudecode/README.md
- eval/memeval/metrics.py
- results/v0.1/README.md
- suggestion.md