## What & why
<!-- One sentence. Link the prd.md goal or plan.md milestone this serves. -->

## Scope (one concern per PR)
- Area / directory touched:
- Owner of that area (per `.github/CODEOWNERS`): @
- [ ] This PR only touches files I own

## Contract impact
- [ ] Does **not** modify `eval/memeval/schema.py` or `eval/memeval/protocols.py` (the frozen contract)
- [ ] If it does, this is a **`[CONTRACT]`** PR (title prefix) and I have:
  - [ ] updated `architecture.md` in this same PR
  - [ ] listed every affected dependent + owner below
  - [ ] requested review from **all** contract owners

### Affected dependents (only for `[CONTRACT]` PRs)
| Module | Owner | Migration |
|---|---|---|
|  |  |  |

## Checklist
- [ ] Branch is short-lived and up to date with `main`
- [ ] `cd eval && python tests/test_smoke.py` passes (CI runs it too)
- [ ] I only reformatted lines I changed
- [ ] No `__pycache__` / `.pytest_cache` / secrets committed
