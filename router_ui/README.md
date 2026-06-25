# Router Memory Inspector

A local web UI to inspect the memories the **cookbook-memory plugin** saves during a benchmark run,
and evaluate how well the **router** routes them. Reads primarily through `memeval`'s public
store/router APIs; the one deliberate exception is the backend-artifact popover, which reads a
memory's actual `.md` file to show its OKF frontmatter + body. It never writes to the substrate
(the eval-capture feature appends to its own `captured_cases.jsonl`), and never parses `.db` files
(vector/graph artifacts are surfaced via the store APIs plus their on-disk paths).
Additive, read-only, zero extra dependencies.

## Run

```bash
./router_ui/run.sh                  # newest pipeline substrate (results/v*/_memory)
./router_ui/run.sh --seed --open    # synthetic demo corpus (no real run needed) + open browser
./router_ui/run.sh --store /path/to/_memory
```

Then open the printed `http://127.0.0.1:8765`.
Flags: `--store DIR`, `--port N` (8765), `--profile speed|fusion|accuracy|auto`, `--seed [--force]`,
`--open`, `--margin-threshold F`.

The **store** field in the header is editable: type another `.../_memory` (or a run dir with a
nested `.cookbook-memory`) and press **Load** (or Enter) to switch substrates **live — no restart**
(`POST /api/reopen` reopens the substrate server-side and refreshes every view).

## Views

- **Browse** — every memory, with per-backend membership chips. **Click a chip** to open a
  popover of that backend's **stored artifact**: markdown shows the real `.md` file (OKF
  frontmatter + body), vectors shows the stored record + embedding metadata (dim/model/index),
  graph shows the node + its typed edges — each with a **Copy path** button (the per-memory
  `.md` file for markdown; the shared `memory.db` / `graph.db` for vectors / graph). Click a
  **row** for the full memory detail (content, metadata, edges).
- **Routing-effectiveness** — actual on-disk landing vs the router's `classify` + `write_plan`, with
  ⚠ flags for **write-plan-vs-actual asymmetry** and **low classifier margin** (the real mis-route
  signals under the default `base_all` policy, where every memory fans out to all three backends).
- **Query Probe** — a query's routing decision + raw per-backend results (score semantics labeled) +
  the routed engine answer.
- **Capture as eval case** — appends to `router_ui/captured_cases.jsonl`, feeding the fast unit-eval tier.

## How it runs

Run from the repo root. `run.sh` puts the repo root on `PYTHONPATH` (so `router_ui` resolves) and
`cd`s there so `results/` auto-discovery works; it uses the repo's `.venv` (which provides `memeval`
after `make setup`), falling back to `python3`. The inspector is stdlib-only — no extra dependencies.

Tests (dev only — needs `pytest` + `memeval` importable), from the repo root:

```bash
PYTHONPATH=. python -m pytest router_ui/tests/ -q   # 24 tests, real-substrate validated
```
