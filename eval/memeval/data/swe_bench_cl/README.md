# Vendored dataset: SWE-Bench-CL

This directory holds a byte-exact, vendored copy of the **SWE-Bench-CL** dataset
so the harness can run the benchmark fully **offline** and **reproducibly** — no
HuggingFace download at eval time.

## Source

- **HuggingFace dataset:** [`thomasjoshi/swe-bench-cl`](https://huggingface.co/datasets/thomasjoshi/swe-bench-cl)
  (file `SWE-Bench-CL.json`)
- **Paper:** *SWE-Bench-CL: Continual Learning for Coding Agents* — arXiv:[2507.00014](https://arxiv.org/abs/2507.00014)

SWE-Bench-CL is a **continual-learning reorganization of SWE-bench Verified**:
the verified instances are regrouped into chronologically ordered, per-repository
**sequences** so that continual-learning behaviour (forgetting, forward transfer,
memory carry-over) can be measured.

## What's here

| File | Description |
|------|-------------|
| `SWE-Bench-CL.json` | The full dataset (the single JSON file the HF repo ships). |

### Contents

- **File size:** 5.6 MB (5,862,595 bytes)
- **Tasks:** 273
- **Sequences:** 8 (one per repository), chronologically ordered

| Sequence (repo) | Tasks |
|-----------------|------:|
| django/django | 50 |
| sympy/sympy | 50 |
| sphinx-doc/sphinx | 44 |
| matplotlib/matplotlib | 34 |
| scikit-learn/scikit-learn | 32 |
| astropy/astropy | 22 |
| pydata/xarray | 22 |
| pytest-dev/pytest | 19 |

### Integrity

```
sha256  91bc39a769b6218419bd44308650e5d2c846ecd3e6f7a6c086f74a37b6db90f7  SWE-Bench-CL.json
```

The file is committed verbatim. The repo-root `.gitattributes` marks
`eval/memeval/data/**` as `-text` so git performs **no EOL conversion** —
the bytes (and therefore the sha256 above) are preserved on every platform.

## Why vendored

Vendoring the dataset gives us:

- **Offline runs** — the loader resolves this local copy first, so evaluation
  needs no network access and no `datasets` / `huggingface_hub` install.
- **Reproducibility** — the exact bytes used for every run are pinned in-tree
  and verifiable against the recorded sha256, immune to upstream edits or a
  vanished HuggingFace snapshot.

## How the loader uses it

`memeval.loaders.swe_bench_cl.SWEBenchCLLoader` resolves its source with this
precedence:

1. **Explicit path / source** passed to `load(...)` — used as-is (a local file
   is parsed offline; a HF id / URL hits the remote path).
2. **This vendored copy** — when no explicit source is given and this file
   exists, the loader parses it offline (stdlib `json` only).
3. **HuggingFace remote** (`thomasjoshi/swe-bench-cl`) — fallback only when the
   vendored file is absent.

So a default `get_loader("swe_bench_cl").load(None)` returns all 273 tasks from
this file with no network access. The file is also shipped as package data (see
`eval/pyproject.toml`), so it remains resolvable from an installed wheel.

## License / attribution

The instances derive from upstream open-source repositories (django, sympy,
sphinx, matplotlib, scikit-learn, astropy, xarray, pytest) by way of **SWE-bench
Verified**, then reorganized by SWE-Bench-CL. The file is redistributed here
**for research evaluation only**. Downstream users should confirm
license compatibility (the upstream projects and SWE-bench) for their particular
use. See the source dataset card and paper above for upstream provenance.
