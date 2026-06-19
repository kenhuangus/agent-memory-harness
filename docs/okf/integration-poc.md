# Open Knowledge Format (OKF) → Cookbook Memory: research + integration POC

**Status:** proof-of-concept (Ken). Code: [`eval/memeval/okf.py`](../../eval/memeval/okf.py).
Tests: `eval/tests/test_smoke.py` (`-k okf`, 4 tests, all green).

## 1. What OKF is

OKF (Google Cloud, **v0.1**) is an open, vendor-neutral spec for representing
knowledge as **a directory of markdown files with YAML frontmatter**, cross-linked
into a graph. Sources:
- Article: <https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing>
- Repo / spec / samples: <https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf> (`SPEC.md`, `bundles/{ga4,stackoverflow,crypto_bitcoin}`, `src/enrichment_agent`)

The whole spec fits on a page. The essentials:

- **One required field:** `type` (a free string, e.g. `BigQuery Table`, `Playbook`).
  Consumers must tolerate unknown types.
- **Recommended fields:** `title`, `description`, `resource` (URI of the asset),
  `tags` (list), `timestamp` (ISO-8601). Producers MAY add custom keys; consumers
  MUST preserve unknown keys and never reject docs for them.
- **Body:** plain markdown, conventional headings (`# Schema`, `# Examples`, `# Citations`).
- **Links:** ordinary markdown links are **directed edges of an untyped graph**;
  the relationship meaning is in the prose. Consumers tolerate broken links.
- **Reserved files:** `index.md` (progressive-disclosure listing) and `log.md`
  (chronological change history). Root `index.md` may carry `okf_version: "0.1"`.
- **Conformance (hard rules only):** every non-reserved `.md` has parseable
  frontmatter with a non-empty `type`. Everything else is soft guidance.

## 2. Why it fits our memory system

A memory store *is* curated, typed, timestamped, tagged, cross-linked notes — the
same shape OKF standardizes. The mapping is near 1:1:

| `MemoryItem` field | OKF | Notes |
|---|---|---|
| `content` | markdown body | as-is |
| `source` | `type` (required) | e.g. "session", "agent", "BigQuery Table" |
| `item_id` | `resource` (`memeval://memory/<id>`) + filename | stable identity |
| `tags` | `tags` | direct |
| `timestamp` (epoch) | `timestamp` (ISO-8601) | converted both ways |
| `relevancy`,`version`,`session_id`,`tokens` | custom `x_*` keys | lossless round-trip |
| `metadata` | `x_metadata_json` + `title`/`description` | preserved |
| (memory→memory references) | markdown links | `metadata["okf_links"]` = graph edges |

This unlocks three concrete integrations:

1. **Interchange / portability.** Export a `MemoryStore` to an OKF bundle that *any*
   OKF consumer reads — Google's Knowledge Catalog, the OKF force-directed
   visualizer, or **another agent harness**. Import bundles others produced as
   memory. This is the concrete seam for the cross-harness memory-sharing work in
   [`docs/harnesses/`](../harnesses/README.md).
2. **Brent's markdown backend, standardized.** `stores/markdown_store.py` is specced
   as "memory as markdown + YAML frontmatter" — that *is* OKF. `OKFStore` is a
   working reference of exactly that, conformant to a published spec, so a run's
   memory is a portable bundle on disk.
3. **Dreaming / governance (Scott) get OKF's `log.md` + `index.md`.** Our
   `MemoryItem.version` maps onto `log.md` change entries; consolidation passes can
   rewrite `index.md` for progressive disclosure; the link graph feeds the graph
   store and conflict resolution.

## 3. The POC

[`eval/memeval/okf.py`](../../eval/memeval/okf.py) — stdlib-only (a minimal
frontmatter reader/writer), uses PyYAML when installed for robust parsing of
arbitrary foreign bundles. API:

- `memory_item_to_doc(item)` / `doc_to_memory_item(text)` — lossless mapping.
- `export_bundle(items, dir)` — conformant bundle: `<type>/<id>.md` docs + per-type
  `index.md` + root `index.md` (`okf_version`) + `log.md`.
- `import_bundle(dir)` — parse ours **or foreign** bundles → `MemoryItem`s.
- `validate_bundle(dir)` — the hard conformance rules.
- `OKFStore(path)` — a `MemoryStore` whose persistence *is* an OKF bundle (write
  emits docs; search reuses the reference ranking; a fresh store autoloads from disk).

Verified (POC driver + tests):
- **Lossless round-trip** of every `MemoryItem` field; links extracted as edges.
- **Conformant export** (`validate_bundle == []`) with the OKF directory layout.
- **Foreign import**: parses Google's actual `stackoverflow/tables/users.md`
  (multi-line description, block-list tags, quoted timestamp, no `x_` keys).
- **Live backend**: `harness.run(..., store=OKFStore(dir))` runs a benchmark and
  leaves a conformant OKF bundle on disk.

## 4. Suggested next steps

- Have Brent's `MarkdownStore` delegate to `OKFStore` (one inverted-keyword index on
  top) so the markdown backend is OKF-native.
- A CLI: `python -m memeval.okf export --results-store … --out bundle/` and `import`.
- Wire the OKF link graph into the graph-store backend; wire `log.md` to the
  dreaming worker's versioning so consolidation writes OKF history.
- Emit the OKF `viz.html` (force-directed graph) for a run's memory for debugging.
- Track OKF spec versions; `okf_version` is already written to the root index.
