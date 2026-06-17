# Plan — Cookbook Memory (coordination contract)

> **Coordination contract** (the *who & when*). Governs how the other two
> contracts change. The detailed narrative lives in [`project-plan.md`](project-plan.md);
> this file is the lean, enforceable version four people work against.

**Repository:** <https://github.com/kenhuangus/agent-memory-harness> · **Live site:** <https://kenhuangus.github.io/agent-memory-harness/>

## Team
| | Owner | Area |
|---|---|---|
| **P1** | **Keith** | Harness architecture + OpenCode integration |
| **P2** | **Ken** | Evaluation infrastructure (`eval/` package) + the static site |
| **P3** | **Brent** | Storage backends + retrieval router |
| **P4** | **Scott B.** | Async "dreaming": dedup, conflict resolution, governance |

## Ownership map (enforced by [`.github/CODEOWNERS`](.github/CODEOWNERS))
Work in branches prefixed by your area; the directories you own gate the PR.

| Developer | Owns (paths) | Branch prefixes |
|---|---|---|
| **Ken** | `eval/memeval/loaders/`, `metrics.py`, `cost.py`, `trajectory.py`, `agent.py`, `tracing.py`, `results.py`, `eval/tests/`, the site (`*.html`, `assets/`) | `eval/*` · `loaders/*` · `metrics/*` · `eval-infra/*` · `site/*` |
| **Keith** | `eval/memeval/harness.py`, `models.py`, `cli.py`, `opencode/` (memory framework) | `harness/*` · `opencode/*` |
| **Brent** | `eval/memeval/stores/`, `router.py` *(scaffolded — stubs to implement)* | `stores/*` · `router/*` |
| **Scott B.** | `eval/memeval/dreaming/` *(scaffolded — stubs to implement)* | `dreaming/*` |
| **All four** | `schema.py`, `protocols.py` (frozen) + the contract docs | `[CONTRACT] …` (all owners approve) |

## Milestones (two-week sprint)
| Day | Milestone |
|---|---|
| **D3** | **Interface freeze** — `schema.py` + `protocols.py` + stubs locked. The pivotal gate. |
| **D5** | End of week 1 — three backends read/write; run harness + loaders ready; baselines started. |
| **D8** | Integration start — router + orchestrator connected; dreaming runs against real stores. |
| **D10** | Ship — all benchmark shards complete; four metrics aggregated vs. baselines. |

## Dependency graph
The chain that sets the critical path (see the visual on the
[plan page](https://kenhuangus.github.io/agent-memory-harness/plan.html#dependencies)):

- **Keith + Brent (D1–D3)** co-author & **freeze** the storage interface + schema → unblocks everyone.
- **Brent + Scott B. → Keith:** the OpenCode memory framework (`opencode/`) wraps the
  agent loop around Brent's stores/router (store + retrieve) and Scott's dreaming, so
  both must land before Keith's framework integrates end-to-end (week 2).
- **Ken → all (by D5):** datasets + trajectory logging + the shared run harness (incl.
  the `AgentAdapter` seam in `agent.py` that OpenCode plugs into), so baselines can start ~D4–D6.
- **Brent + Keith → Scott B.:** the dreaming worker integrates once real storage exists (D8+).
- **Sharded keys:** each captain runs on a separate API budget — baselines week 1, treatment week 2.

> After Day 3 everyone is unblocked: they build against the **Protocols**, not each
> other's code. Pre-freeze, the only hard dependency is "Keith ships the stubs."

## Interface-freeze policy
After Day 3, `schema.py` / `protocols.py` change **only** via the contract-change
process below. Build against them; never edit them casually.

## Contract-change process
1. Title the PR **`[CONTRACT] …`**.
2. Edit `schema.py`/`protocols.py` **and** [`architecture.md`](architecture.md) in the same PR.
3. Fill the PR template's "Affected dependents" table (module · owner · migration).
4. Get approval from **all four** owners — enforced by `.github/CODEOWNERS` on the frozen files.
5. CI must stay green; merge only after dependents confirm or the PR includes their migration.

## How we avoid conflicts (summary)
GitHub Flow + short-lived branches + small PRs · one owner per directory ·
`main` protected (PR + code-owner review + green CI) · stubs committed first ·
don't reformat files you don't own. Full rules in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Cadence & Definition of Done
- Daily 5-min async standup thread; a "I'm about to touch a shared file" heads-up.
- **DoD per milestone** ties back to the [`prd.md`](prd.md) success metrics.
