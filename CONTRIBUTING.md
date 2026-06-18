# Contributing to Cookbook Memory

Four developers, one repo. These rules let us work in parallel without merge
conflicts. Read the three **contracts** first — they are the source of truth:

| Contract | File | Answers |
|---|---|---|
| Product | [`prd.md`](prd.md) | **what & why** — problem, goals, non-goals, success metrics |
| Technical | [`architecture.md`](architecture.md) | **how & where** — module boundaries, frozen interfaces, directory ownership |
| Coordination | [`plan.md`](plan.md) | **who & when** — milestones, owner map, dependency graph, change process |

(`project-plan.md` is the longer narrative version; the live site renders it.)

## Branching — GitHub Flow with trunk discipline
1. Branch from `main`: `git switch -c <area>/<short-desc>` (e.g. `loaders/add-locomo`).
2. Keep branches **short-lived** — merge within 1–2 days. Long branches cause conflicts.
3. Sync daily: `git fetch && git rebase origin/main`. Resolve drift in small bites.
4. Open a small PR (one concern, ideally < ~400 lines changed).
5. `main` uses **non-blocking** protection — land work via PR, but **no approvals
   or passing checks are required to merge**: every PR is eligible by default and
   **any collaborator can merge**. CI + code-owner requests run as signals.

## Ownership — one owner per directory
See [`.github/CODEOWNERS`](.github/CODEOWNERS). Edit only the paths you own; CODEOWNERS
auto-requests the path's owner as a reviewer — review is a **courtesy, not a hard
gate**. **Don't reformat files you don't own** (whole-file reflow = guaranteed conflict).

Owner map:

| Area | Owner |
|---|---|
| `eval/memeval/{schema,protocols}.py` (frozen) | **all four** |
| `eval/memeval/{harness,models,cli}.py`, the site | **Keith** |
| `eval/memeval/{loaders/,metrics,cost,trajectory}.py`, `eval/tests/` | **Ken** |
| `eval/memeval/stores/`, `router.py` | **Brent** |
| `eval/memeval/dreaming/` | **Scott B.** |

## The frozen contract
`schema.py` and `protocols.py` are **frozen** (Day 3). Build *against* them — add a
backend / loader / model adapter by implementing the relevant `typing.Protocol`
in your **own** directory; no contract edit required.

### Changing the contract (rare)
1. Title the PR `[CONTRACT] …`.
2. Edit `schema.py`/`protocols.py` **and** `architecture.md` in the **same** PR.
3. Fill the "Affected dependents" table in the PR template.
4. By **team convention**, loop in all four owners (CODEOWNERS auto-requests them) — a courtesy for contract changes, not a hard gate.

## Stubs first
Before building a dependent, make sure the interface stub is on `main`
(`InMemoryStore`, `EchoModel` are the reference stubs). No dependent merges
before the stub it relies on.

## Before you push
- `cd eval && python tests/test_smoke.py` is green (CI runs it on every PR).
- Lint clean; no `__pycache__`, `.pytest_cache`, or secrets.
