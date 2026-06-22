# Knowledge Base — eval

**Domain owner:** Ken
**First entry:** 2026-06-22

Append-only journal of project-story snapshots for the **eval** workstream.
See [README.md](README.md) for conventions.

---

## 2026-06-22T11:32 — entry 1

**Triggered by:** Initial KB seeding via cross-cutting `/kb all` run — establishes baseline state of the eval workstream as the `.kb/` convention lands in the repo.
**Branch:** harness/add-kb-command
**Related ADRs:** ADR-eval-001
**Cross-domain run:** [KB-harness.md](KB-harness.md), [KB-storage.md](KB-storage.md), [KB-dreaming.md](KB-dreaming.md)

### Summary
The eval workstream owns the black-box boundary between the memory system and how we know it works — the `eval/memeval/` package (loaders, agent, metrics, cost, trajectory, tracing, results), the benchmark protocol, and the integrity discipline that keeps scoring honest. v0.1 baseline results landed in PR #36 (Claude Code built-in vs plugin memory across five benchmarks); since then the focus has been making the scoring honest before the treatment runs land: BM25 replaced length-coupled Jaccard for the QA scorer (PR #43), the QA grader and CODE grader were corrected (PR #59), the trajectory log now persists `is_gold` (PR #46), and the plugin-real eval mode (PR #53) benchmarks the shipping plugin as a black box rather than a mocked memory layer. The sprint hypothesis is unchanged: **Haiku + harness beats Opus 4.8 no-memory on ≥ 2 of 5 benchmarks** across the four metrics (Recency, Efficiency, Relevancy, Accuracy) without > ~10% memory-token overhead.

### Key state
ADR-eval-001 extracted the memory system into its own package; `memeval` stays pure eval — that boundary is what makes the plugin-real eval mode meaningful (a memory implementation can be swapped at the contract seam without touching benchmark code). The SWE-bench Docker grader is wired in for CODE benchmarks (PR #32); the offline default grader stays string/overlap-based so the zero-dependency path keeps working. Cost tracking is committed against the Anthropic price list — Haiku 4.5 $1/$5 per Mtok in/out, Sonnet 4.6 $3/$15, Opus 4.8 $5/$25 — with a $10 default per-run budget (override via `--budget-usd`, set ≤0 to disable). The `memeval-bench` command can run any benchmark on its own with `--list-benchmarks` for discovery (PR #33). The four PRD success metrics map 1:1 to `eval/memeval/metrics.py`.

### Open items
- v0.1 baseline results are honest reads (the plugin-real mode + corrected graders), but treatment runs (Haiku + harness vs. Opus no-memory) have not yet been completed across all five benchmarks. The PRD compliance audit and ablation survey landed in PR #61 to frame what success looks like; the data hasn't.
- Real CODE scoring via SWE-bench Docker is opt-in per benchmark (`swe_contextbench`, `swe_bench_cl`, `contextbench`); the cloud `sb-cli` fallback is documented but not exercised in CI.
- Reranker integration (Voyage `rerank-2.5` per PRD §7.1) is decided but not yet wired into the retrieval path on the storage side; the eval will need a reranker-on/reranker-off ablation once it lands.

### Artifacts at time of entry
- [`prd.md`](../prd.md) — success metrics, must-have decisions
- [`architecture.md`](../architecture.md)
- [`plan.md`](../plan.md)
- [`prd-plan-compliance.md`](../prd-plan-compliance.md)
- [`analysis-ablation-survey.md`](../analysis-ablation-survey.md)
- [`benchmark-schema-sampledata.md`](../benchmark-schema-sampledata.md)
- [`results/v0.1/README.md`](../results/v0.1/README.md) — v0.1 baseline results
- `eval/memeval/` — loaders, agent, metrics, cost, trajectory, tracing, results
- [`docs/adrs/ADR-eval-001-extract-memory-package.md`](../docs/adrs/ADR-eval-001-extract-memory-package.md)
