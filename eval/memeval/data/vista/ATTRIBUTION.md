# VISTA Bench — vendored corpus subset (attribution)

The files in this directory are derived from **VISTA Bench**
(https://github.com/kenhuangus/vista-benchmark), a deterministic benchmark for
long-running agents (foresight, calibrated escalation, injection resistance, and
self-improvement safety).

* `vista_corpus.jsonl` — the VISTA "journey" corpus (6 route-graph journeys,
  spanning the `drift` / `slow_burn` / `injection` event types). Vendored
  verbatim for OFFLINE, no-network tests as a small fixture subset. This is the
  loader's default offline fixture.
* `full/` — the FULL upstream VISTA corpus, vendored verbatim for offline,
  no-network use (opt-in via the loader; the curated subset above stays default):
    - `full/vista_corpus.jsonl` — the complete corpus (390 journeys).
    - `full/splits/{train,dev,test,challenge}.jsonl` — the canonical splits
      (99 / 97 / 97 / 97; sum 390).
    - `full/dataset_summary.json` — the upstream breakdown (domain, difficulty
      tier, attack ASI, split, source) + canonical `schema_keys`.
    - `full/PROVENANCE.md` — upstream commit SHA + pull date for these files.
  These files are redistributed **unmodified** under CC-BY-4.0 with attribution
  preserved.
* `human_validated_subset.json` — VISTA's both-polarity, human-adjudicated gold
  subset (`validation/gold/human_validated_subset.json`), used by the
  grader↔human agreement methodology utility.

## License

VISTA's corpus is published under **CC-BY-4.0**
(https://creativecommons.org/licenses/by/4.0/).

**Attribution:** "VISTA Bench" by Ken Huang et al., https://github.com/kenhuangus/vista-benchmark,
licensed under CC-BY-4.0. The files here are redistributed unmodified under the
terms of that license, with attribution preserved.

These data files are used as evaluation fixtures only; no VISTA source code is
forked or vendored — the harness ports the *patterns* into harness-native,
stdlib-only modules.
