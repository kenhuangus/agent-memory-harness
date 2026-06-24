# VISTA Bench — vendored corpus subset (attribution)

The files in this directory are derived from **VISTA Bench**
(https://github.com/kenhuangus/vista-benchmark), a deterministic benchmark for
long-running agents (foresight, calibrated escalation, injection resistance, and
self-improvement safety).

* `vista_corpus.jsonl` — the VISTA "journey" corpus (6 route-graph journeys,
  spanning the `drift` / `slow_burn` / `injection` event types). Vendored
  verbatim for OFFLINE, no-network tests as a small fixture subset.
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
