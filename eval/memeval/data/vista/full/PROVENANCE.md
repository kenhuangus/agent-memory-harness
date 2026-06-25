# VISTA full corpus — provenance

Vendored verbatim from the public upstream repository:

* Source: https://github.com/kenhuangus/vista-benchmark (path `huggingface/`)
* Upstream commit SHA: `81acf0418af14d8372c89557411d06bd654e05ad`
* Pulled: 2026-06-25 (raw.githubusercontent.com @ `main`)
* License: **CC-BY-4.0** (attribution preserved — see `../ATTRIBUTION.md`)

## Files (verified line counts)

| File | Records |
|------|---------|
| `vista_corpus.jsonl` | 390 |
| `splits/train.jsonl` | 99 |
| `splits/dev.jsonl` | 97 |
| `splits/test.jsonl` | 97 |
| `splits/challenge.jsonl` | 97 |

Split sum = 390, matching the full corpus.

`dataset_summary.json` carries the upstream breakdown (by domain, difficulty
tier, attack ASI category, split, and source) plus the canonical `schema_keys`.

These files are redistributed unmodified. The small 6-journey curated fixture at
`../vista_corpus.jsonl` is unrelated to these files and remains the default
offline fixture for the loader.
