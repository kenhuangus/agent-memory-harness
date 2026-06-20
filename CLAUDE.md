# Cookbook Memory — repo instructions

A 4-person, two-week sprint building a model-agnostic memory harness for
long-running coding agents. Read the three contracts first — they are the source of
truth: [`prd.md`](prd.md) (what & why), [`architecture.md`](architecture.md) (how &
where), [`plan.md`](plan.md) (who & when). Contributor workflow lives in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Architecture Decision Records (ADRs)

Load-bearing technical decisions are recorded as ADRs under
[`docs/adrs/`](docs/adrs/) — one file per decision, with the full WHY (context,
options, decision, rationale, tradeoffs, consequences). See
[`docs/adrs/README.md`](docs/adrs/README.md) for the schema, the decision index, and
the full how-to.

**When to write one:** when a decision is load-bearing and not obvious — a future
collaborator would reasonably ask *"why that and not the obvious alternative?"* and
the answer isn't already self-evident from the code or the frozen contract. Write one
for a real choice between alternatives (a backend, a trigger, a tool/wire schema, an
isolation model, a trust boundary), anything that **establishes a contract** other
workstreams build against, or a decision that **reverses** an earlier one. Don't
write one for reversible implementation details or anything already determined by an
accepted ADR.

**How:**

1. Name it `ADR-<domain>-NNN-<slug>.md`, numbered **per domain** (`harness` = Keith,
   `storage` = Brent, `dreaming` = Scott, `eval` = Ken). Use the next integer for
   your domain; never re-number.
2. Copy the schema from [`docs/adrs/README.md`](docs/adrs/README.md) and fill **every**
   section — an empty "Tradeoffs & risks" means the decision wasn't really made.
3. Set `contract: true` when multiple workstreams build against the shape, and name
   its source of truth, shape, and exhaustive consumers.
4. Add a row to the decision index in `docs/adrs/README.md`.
5. Land it via PR (only your domain's ADRs); a decision touching the frozen contract
   (`schema.py`/`protocols.py` + `architecture.md`) still follows the `[CONTRACT]` PR
   process.
6. To change a decision, write a **new** ADR that `supersedes:` the old one and flip
   the old one's `status:` to `Superseded` — never rewrite an accepted ADR's body.

## Working in this repo

- **Group project, trunk discipline.** Branch off `main` (`<area>/<short-desc>`),
  keep branches short-lived, land via small PRs (see CONTRIBUTING.md). Don't commit
  feature/doc work straight to `main`.
- **One owner per directory** ([`.github/CODEOWNERS`](.github/CODEOWNERS)). Edit only
  the paths you own; don't reformat files you don't own.
- **The frozen contract** (`eval/memeval/schema.py`, `protocols.py`) is frozen — build
  *against* it; change it only via a `[CONTRACT]` PR with all owners looped in.
