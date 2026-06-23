# INITIAL_DREAM_RUBRIC.md — first substantive `DreamingWorker.run()` body

**Scope.** Job 1 of ADR-dreaming-002 *only*, **detection half only**: walk `store.all()`,
group items by a normalized-content key, return a JSON-serializable governance summary
dict. No item mutation. No retirement. No merge. No embedding. No trajectory reading.

**Targets.**
- `eval/memeval/dreaming/worker.py` — `DreamingWorker.run` body (the module-level `dream()`
  wrapper requires no change).
- New unit tests under `eval/memeval/dreaming/tests/test_worker.py` (file does not yet
  exist; the rubric's "unit test" criteria authorize its creation).

**Supersedes.** `PR5_DAYDREAM_CLI_RUBRIC.md` §H criteria **49, 50, 51, 53** — the
carve-out's "stub is acceptable / `worker.py` byte-identical" allowance for
`DreamingWorker.run`. 49 (exit-0 on `NotImplementedError`), 50 (`dream_all_skipped`
event), 51 (visible-log on `NotImplementedError`), and 53 (`worker.py` unchanged) all
assumed the v1 stub; this rubric replaces the stub with the dedup-detection body so the
`NotImplementedError` path no longer exists on success. The fail-open contract from §H
**52, 54, 55** is preserved (see §H of this rubric) and not superseded.

**Format law.** Every criterion is PASS / FAIL / N-A. No "mostly," "should,"
"approximately." Each names its verification mode — `unit test` or `shell command` —
verbatim. No compound criteria (no "and/or" in a single line; split if needed).

---

## A. Surface — `run()` no longer raises

- [ ] **A1.** `DreamingWorker(store).run()` over a store containing one `MemoryItem` returns a `dict` and does not raise. **Verify:** unit test `test_run_returns_dict_for_single_item`. **Boolean check:** `isinstance(result, dict)` AND no exception.
- [ ] **A2.** `DreamingWorker(store).run()` over an empty store (`store.all()` returns `[]`) returns a `dict` and does not raise. **Verify:** unit test `test_run_empty_store_returns_dict`.
- [ ] **A3.** `memeval.dreaming.worker.dream(store)` (module-level wrapper) returns the same dict shape as `DreamingWorker(store).run()` (object-equal). **Verify:** unit test `test_dream_wrapper_matches_worker`.
- [ ] **A4.** `worker.py` contains zero `raise NotImplementedError` lines anywhere within the `DreamingWorker.run` method body. **Verify:** shell command `! grep -nE 'raise[[:space:]]+NotImplementedError' eval/memeval/dreaming/worker.py`.

## B. Dict shape — exact keys, types, JSON-serializable

The proposed schema is the contract. Every key is required; extras are forbidden in v1
(the rubric author pushes back if extras are needed — see §J).

Required top-level keys with types:
- `schema: str` — fixed literal `"dream.summary"` (envelope discriminator).
- `version: int` — fixed literal `1`.
- `mode: str` — fixed literal `"detection"`.
- `jobs_run: list[str]` — exactly `["dedup_detection"]`.
- `skipped_jobs: list[str]` — exactly `["dedup_merge", "contradiction_resolution", "governance", "pruning"]` (order pinned).
- `counts: dict[str, int]` — exactly the keys `total_items`, `duplicate_clusters`, `items_in_duplicates`; values are `int`.
- `clusters: list[dict]` — each cluster has exactly the keys `normalized_key: str`, `item_ids: list[str]`, `count: int`.

Criteria:

- [ ] **B1.** The returned dict's top-level key set equals exactly `{"schema","version","mode","jobs_run","skipped_jobs","counts","clusters"}`. **Verify:** unit test `test_run_top_level_keys_exact` — `assert set(result.keys()) == EXPECTED_KEYS`.
- [ ] **B2.** `result["schema"] == "dream.summary"` (string-equal). **Verify:** unit test `test_run_schema_literal`.
- [ ] **B3.** `result["version"] == 1` and `type(result["version"]) is int`. **Verify:** unit test `test_run_version_literal`.
- [ ] **B4.** `result["mode"] == "detection"`. **Verify:** unit test `test_run_mode_literal`.
- [ ] **B5.** `result["jobs_run"] == ["dedup_detection"]` (list-equal, single element). **Verify:** unit test `test_run_jobs_run_literal`.
- [ ] **B6.** `result["skipped_jobs"] == ["dedup_merge","contradiction_resolution","governance","pruning"]` (list-equal, order pinned). **Verify:** unit test `test_run_skipped_jobs_literal`.
- [ ] **B7.** `result["counts"]` has key set exactly `{"total_items","duplicate_clusters","items_in_duplicates"}` and every value is an `int` (not `bool`, not `float`). **Verify:** unit test `test_run_counts_shape` — `assert set(result["counts"]) == {...} and all(type(v) is int for v in result["counts"].values())`.
- [ ] **B8.** `result["clusters"]` is a `list`; every element's key set equals exactly `{"normalized_key","item_ids","count"}`; `normalized_key` is `str`; `item_ids` is `list[str]`; `count` is `int` and equals `len(item_ids)`. **Verify:** unit test `test_run_cluster_element_shape`.
- [ ] **B9.** The returned dict round-trips through `json.dumps`/`json.loads` and the loaded value equals the original (`==`). **Verify:** unit test `test_run_result_json_roundtrip` — `assert json.loads(json.dumps(result)) == result`.

## C. Counts — arithmetic consistency

- [ ] **C1.** `result["counts"]["total_items"] == len(store.all())` for any store under test. **Verify:** unit test `test_counts_total_items_matches_store`.
- [ ] **C2.** `result["counts"]["duplicate_clusters"] == len(result["clusters"])`. **Verify:** unit test `test_counts_duplicate_clusters_matches_len`.
- [ ] **C3.** `result["counts"]["items_in_duplicates"] == sum(c["count"] for c in result["clusters"])`. **Verify:** unit test `test_counts_items_in_duplicates_matches_sum`.
- [ ] **C4.** Every cluster has `count >= 2` (singletons are not "duplicate clusters" and are excluded from `clusters`). **Verify:** unit test `test_clusters_have_count_at_least_two`.

## D. Determinism / idempotence

"Structurally equal" is defined as: `counts` and scalar fields equal under `==`; `clusters`
compared as a set of `frozenset(item_ids)` plus a multiset of `(normalized_key, count)`
pairs — order of clusters and order of `item_ids` within a cluster are unspecified.

- [ ] **D1.** Two consecutive `run()` calls over the same unmutated store return dicts that satisfy the structural-equality definition above. **Verify:** unit test `test_run_idempotent_structural_equal` — implements the comparison helper inline.
- [ ] **D2.** Two consecutive `run()` calls return `counts` dicts that are `==`. **Verify:** unit test `test_run_idempotent_counts_equal`.
- [ ] **D3.** Within a single `run()` result, no `item_id` appears in more than one cluster. **Verify:** unit test `test_no_item_id_in_two_clusters` — flatten `item_ids`, assert no duplicates.
- [ ] **D4.** The `item_ids` list inside each cluster contains no duplicate ids. **Verify:** unit test `test_no_duplicate_ids_within_cluster`.

## E. Normalization correctness — positive and negative cases

Normalization rule under test: lowercase, collapse runs of whitespace to a single space,
strip leading/trailing whitespace, strip ASCII punctuation
(`string.punctuation`, applied via `str.translate` or `re.sub`). Stdlib only.

Each criterion uses a freshly-built `InMemoryStore` (or test-double satisfying the
`MemoryStore` protocol) seeded with the named contents.

- [ ] **E1.** Items with contents `"Hello, world!"` and `"hello world"` cluster together (one cluster, `count == 2`, both `item_id`s present). **Verify:** unit test `test_normalization_positive_punct_and_case`.
- [ ] **E2.** Items with contents `"Hello world."` and `"Hi there"` do not cluster (zero entries in `clusters`). **Verify:** unit test `test_normalization_negative_different_content`.
- [ ] **E3.** Items with contents `"foo   bar"` and `"foo bar"` cluster together (whitespace collapse). **Verify:** unit test `test_normalization_whitespace_collapse`.
- [ ] **E4.** Items with contents `"  foo bar  "` and `"foo bar"` cluster together (leading/trailing strip). **Verify:** unit test `test_normalization_strip_edges`.
- [ ] **E5.** Three items with contents `"Hello!"`, `"hello"`, `"Hello, "` produce one cluster with `count == 3` and all three `item_id`s. **Verify:** unit test `test_normalization_three_member_cluster`.
- [ ] **E6.** An item with empty `content == ""` does not raise. **Verify:** unit test `test_normalization_empty_content_does_not_raise`. (Whether empty-content items cluster with other empty-content items is the implementer's call; this criterion only requires no exception.)
- [ ] **E7.** An item with `content is None` does not raise (`MemoryItem.content` is typed `str` but Python doesn't enforce that at runtime; `store.all()` is the worker's trust boundary). Implementer contract: treat `None` as the empty string for normalization purposes and proceed. **Verify:** unit test `test_normalization_none_content_does_not_raise` — seed store with a `MemoryItem` whose `content` is forcibly `None` (via `object.__setattr__` to bypass `slots=True` type hint); assert `run()` completes; assert the resulting cluster (if any) is well-formed.

## F. No mutation — store is read-only across the call

This is the critical contract guard. The detection-only worker MUST NOT call
`store.write` and MUST NOT alter any `MemoryItem` field.

- [ ] **F1.** The set `{(item.item_id, item.version) for item in store.all()}` is identical before and after `DreamingWorker(store).run()`. **Verify:** unit test `test_run_does_not_mutate_id_version_set`.
- [ ] **F2.** For every `item_id` present before the call, `store.get(item_id).content` is byte-identical before and after the call. **Verify:** unit test `test_run_does_not_mutate_content`.
- [ ] **F3.** For every `item_id` present before the call, `store.get(item_id).relevancy` is float-equal before and after the call (slop guard against a "soft delete" that drops `relevancy` to `0.0`). **Verify:** unit test `test_run_does_not_mutate_relevancy`.
- [ ] **F4.** A spy `MemoryStore` whose `write` method records all calls receives zero `write` calls during a `run()` over a seeded store. The spy implements all four protocol methods (`write`, `get`, `search`, `all`) so it satisfies `@runtime_checkable MemoryStore`; only `write` is counted. **Verify:** unit test `test_run_makes_zero_write_calls` — uses a fake store protocol implementation.
- [ ] **F5.** `worker.py` source contains zero literal occurrences of `store.write` and zero literal occurrences of `.write(`. **Verify:** shell command `! grep -nE '(store\.write|\.write\()' eval/memeval/dreaming/worker.py`.
- [ ] **F6.** `DreamingWorker(store)` must not mutate any `MemoryItem` object returned by `store.all()` (guards mutation-via-reference on disk-backed stores where the in-memory list is decoupled from persisted state). **Verify:** unit test `test_run_does_not_mutate_returned_items` — `items = store.all(); before = [(i.item_id, i.content, i.relevancy, i.version) for i in items]; DreamingWorker(store).run(); after = [(i.item_id, i.content, i.relevancy, i.version) for i in items]` (re-iterate the SAME list reference, not refetch); assert `before == after`.

## G. `trajectories_path` accepted-and-error (not accepted-and-ignored)

**Posture change from prior draft.** Halliday review #1: accept-and-silently-ignore on a typed `str | None` parameter is the months-to-diagnose bug shape. v1 accepts the kwarg in the signature (preserves the v2 expansion path) but rejects truthy values at runtime so the caller learns immediately rather than discovering at v2 that their path was silently dropped.

- [ ] **G1.** `DreamingWorker(store).run(trajectories_path=None)` returns the same dict as `DreamingWorker(store).run()`. **Verify:** unit test `test_run_trajectories_path_none_no_effect`.
- [ ] **G2.** `DreamingWorker(store).run(trajectories_path="/path/that/does/not/exist")` raises `ValueError` with a message substring `"trajectories_path not consumed in v1"`. **Rationale for `ValueError`:** the CLI catches `NotImplementedError` as the v1-stub skip path (cli.py:232-236) and would silently swallow this; `ValueError` falls through to the CLI's generic `except Exception` branch which emits `daydream.dream_all_error` (cli.py:238-244) — still exit-0 fail-open, but observably distinct from the skip path. **Verify:** unit test `test_run_trajectories_path_truthy_raises_valueerror` — asserts `pytest.raises(ValueError, match="not consumed in v1")`.
- [ ] **G3.** No filesystem access is attempted against `trajectories_path` during the call (covers the truthy-then-raise path — the raise must happen before any open). **Verify:** unit test `test_run_no_filesystem_access_to_trajectories_path` — monkeypatch `pathlib.Path.open` and `builtins.open` to a counter; call `run(trajectories_path="/bogus")` inside `pytest.raises(ValueError)`; assert the counter is zero.

## H. Fail-open at the CLI boundary — preserve §H of PR5

These criteria preserve the observable fail-open contract that PR5_DAYDREAM_CLI_RUBRIC.md §H
51, 52, 54, 55 established. They are NOT superseded by this rubric. Criteria 49, 50, 53
**are** superseded — `run()` now returns a dict instead of raising `NotImplementedError`,
so the CLI no longer emits `daydream.dream_all_skipped` on the happy path.

- [ ] **H1.** `daydream-cli dream --all` exits 0 on a successful `run()` call. **Verify:** unit test `test_dream_all_exits_zero_on_success` — invokes `cli.main(["dream","--all","--store",tmp_path])` with `MEMORY_STORE` pointing at an empty fixture store; asserts return value is `0`.
- [ ] **H2.** If `DreamingWorker.run` is monkeypatched to raise `RuntimeError("boom")`, `daydream-cli dream --all` still exits 0 AND the CLI emits the event `daydream.dream_all_error` (literal event-name string, pinned verbatim, matching PR5 §H 52). **Verify:** unit test `test_dream_all_failopens_on_runtime_error` — monkeypatch `memeval.dreaming.worker.dream` to raise; assert exit 0; assert event-emit spy received `daydream.dream_all_error`.
- [ ] **H3.** The worker itself does NOT catch `Exception`, `BaseException`, or `SystemExit` internally — it lets the CLI handler catch (per the §H 52 contract). **Verify:** shell command `! grep -nE 'except[[:space:]]+(Exception|BaseException|SystemExit)[[:space:]]*[:,]' eval/memeval/dreaming/worker.py`. (Narrow excepts for specific identified failure modes are allowed if a follow-up rubric criterion documents them; none are required for v1.)
- [ ] **H4.** `KeyboardInterrupt` raised inside `DreamingWorker.run` propagates out of the CLI — mirror of PR5 §H 55. **Verify:** unit test `test_dream_all_does_not_swallow_keyboardinterrupt` — monkeypatch `worker.dream` to raise `KeyboardInterrupt`; assert `cli.main` re-raises (does not return 0).
- [ ] **H5.** `SystemExit` raised inside `DreamingWorker.run` propagates out of the CLI — mirror of PR5 §H 55. **Verify:** unit test `test_dream_all_does_not_swallow_systemexit` — monkeypatch `worker.dream` to raise `SystemExit(7)`; assert `cli.main` re-raises `SystemExit` (does not return 0).

## I. Observability — `dream.*` event with named fields

**Critical implementation note for the implementer (not a criterion):** `_handle_dream`
in `cli.py:218-246` does NOT open an `event_context`, so `memeval.dreaming.events.emit`
calls inside the worker will degrade to log-only (events.py:122-125). Worker-level
emits are therefore log-observable, not diary-observable. The criteria below test the
`emit` callable directly via monkeypatch, which is the correct verification surface
regardless of whether the CLI later wraps the call in an `event_context`. The author of
this rubric pushes back on the dispatch's "dream.* prefix" framing — see §J pushback 4.

- [ ] **I1.** Exactly one call to `memeval.dreaming.events.emit` is made during a successful `DreamingWorker.run()`. **Verify:** unit test `test_run_emits_exactly_one_event` — monkeypatch `events.emit` to a spy; assert call count is 1.
- [ ] **I2.** The emitted event name is the literal string `"dream.summary"` (matching the dict's `schema` envelope discriminator). **Verify:** unit test `test_run_emit_event_name_literal` — assert `spy.calls[0].args[0] == "dream.summary"`.
- [ ] **I3.** The emit-call kwargs include the keys `mode`, `total_items`, `duplicate_clusters` (named field check, not just count). **Verify:** unit test `test_run_emit_event_required_fields` — assert each key is present in `spy.calls[0].kwargs`.
- [ ] **I4.** The emit-call kwarg values match the returned dict: `kwargs["mode"] == result["mode"]`, `kwargs["total_items"] == result["counts"]["total_items"]`, `kwargs["duplicate_clusters"] == result["counts"]["duplicate_clusters"]`. **Verify:** unit test `test_run_emit_event_values_match_summary`.

## J. Public-protocol-only — no concrete-store reach-through

- [ ] **J1.** `worker.py`'s import block contains only the following non-stdlib imports: `from ..protocols import MemoryStore`, `from ..schema import MemoryItem` (the latter is added if the implementation type-annotates iteration over `store.all()`), and `from .events import emit`. No other `memeval.*` imports. **Verify:** shell command — `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); from_imports=[(n.level,n.module) for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]; bare=[a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]; allowed_from={(0,'typing'),(0,'__future__'),(0,'json'),(2,'protocols'),(2,'schema'),(1,'events')}; allowed_bare={'re','string','json'}; assert all(f in allowed_from for f in from_imports), from_imports; assert all(b in allowed_bare for b in bare), bare; print('OK')"`. (`ast.ImportFrom.module` strips leading dots into `level`; the allow-list is keyed on `(level, module)` to match. `json` is allowed if used. Asserts no `cookbook_memory.*` and no `memeval.harness`, `memeval.markdown_store`, `memeval.router`.)
- [ ] **J2.** `worker.py` contains zero references to `RouterStore`, `MarkdownStore`, `InMemoryStore`, or any concrete backend class name. **Verify:** shell command `! grep -nE '(RouterStore|MarkdownStore|InMemoryStore|VectorStore|GraphStore)' eval/memeval/dreaming/worker.py`.
- [ ] **J3.** `DreamingWorker.run` uses only protocol-declared methods of `MemoryStore` (per `protocols.py:37-63`: `write`, `get`, `search`, `all`). **Verify:** shell command — restricted method check: `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); attrs=sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id=='store'}); print(attrs); assert set(attrs) <= {'all','get','search','write'}"`. (For v1, only `all` should appear; `write`/`get`/`search` are allow-listed for forgiveness but should not be present.) **Known gap (jasnah final review):** the implementation calls `self.store.all()` (instance attribute), not `store.all()`, so this AST query returns `[]` and the assertion holds *vacuously*. The intent (no `store.delete` / `store.tombstone` / `store.mark_retired` reach-through) is preserved because the worker has no other `store.*` references at all, but the check is not load-bearing. **v2 rubric must broaden the AST walk** to also match `ast.Attribute` where `n.value` is itself an `ast.Attribute` with `attr=='store'` and `value=ast.Name(id='self')`.

## K. Explicit non-goals — v1 deliberately does NOT do

This section is the slop-detection fence: a reviewer grading v1 against future scope
will see exactly what is out of scope and not mark a missing thing as a fail.

- [ ] **K1.** v1 does NOT perform contradiction resolution. `skipped_jobs` lists `"contradiction_resolution"`. **Verify:** unit test (already covered by B6); cross-referenced here for review-time clarity.
- [ ] **K2.** v1 does NOT build session governance (must-know / must-do / blacklist). `skipped_jobs` lists `"governance"`. **Verify:** unit test (already covered by B6).
- [ ] **K3.** v1 does NOT perform selective retention or pruning. `skipped_jobs` lists `"pruning"`. **Verify:** unit test (already covered by B6).
- [ ] **K4.** v1 does NOT merge or retire duplicate items. **Verify:** §F suite.
- [ ] **K5.** v1 does NOT use embeddings or any semantic similarity. **Verify:** shell command `! grep -nE '(embedding|cosine|np\.|numpy|voyage|openai|anthropic)' eval/memeval/dreaming/worker.py`.
- [ ] **K6.** v1 does NOT read trajectories. **Verify:** §G suite.
- [ ] **K7.** v1 does NOT use any non-stdlib package. **Verify:** §J1 import-allow-list shell command.

## L. Concurrency carve-out — read-only, no flock required

ADR-dreaming-014 (concurrent daydream flock) and ADR-dreaming-017 (precompact concurrency)
apply to **write paths**. The detection-only worker takes no write path. Cross-session /
multi-`dream-cli`-process concurrency is not yet ADR'd. This is a deliberate, justified
v1 carve-out — not deferred slop.

**Explicit gate for v2:** before the mutation half of the dream worker ships, an ADR
must address multi-process `dream-cli dream --all` against the same `MEMORY_STORE`. Two
processes both calling `store.all()`, both deciding to retire the same loser, will race.
v1 is safe because writes are zero; v2 inherits this gap unless the ADR pins it first.

- [ ] **L1.** `worker.py` contains no `fcntl`, `flock`, `Lock`, or `Lockfile` references. **Verify:** shell command `! grep -nE '(fcntl|flock|Lock|Lockfile)' eval/memeval/dreaming/worker.py`.
- [ ] **L2.** Two `run()` calls executed concurrently (via `threading.Thread`) against the same in-memory store both return structurally-equal results to a single sequential call. **Verify:** unit test `test_run_concurrent_threads_same_store` — spawn two threads each calling `run()`; join; structural-equal compare to a third sequential call. (Note: in-memory store thread safety is the store's contract, not the worker's. This test catches accidental shared mutable state inside the worker itself.)

---

## Rubric Adversarial Pass

**1. What does this rubric miss?**

- **Cluster size bound.** No criterion caps cluster size or total work. A pathological store with 10⁶ identical items produces one cluster of size 10⁶ — `item_ids` becomes a list of 10⁶ strings inside the returned dict. The dict is still correct by every criterion above, but the in-memory + JSON-serialized representation may be impractical. v1 does not yet ADR a cap; the rubric author considered adding a "if cluster.count > N, truncate and set `truncated: true`" criterion and rejected it as scope creep for the initial case. Risk: a future bench feeds a large fixture and the test process OOMs.
- **Time-stable normalized_key across rebuilds.** The rubric pins `normalized_key` as `str` but does not pin its *value*. A future implementer could change the normalization scheme silently (e.g., add Unicode NFC) and §E's positive/negative cases would still pass while the cross-version key string drifts. If consumers downstream cache by `normalized_key`, that cache invalidates silently. The author considered pinning the normalized form of `"Hello, world!"` to a literal string and rejected it as over-fitting v1 to a specific algorithm choice.
- **`MemoryItem.content is None`.** The schema's `content` is typed `str` (`schema.py:189`), but nothing enforces non-`None` at runtime. If a store somehow yields `content = None`, the normalization step will raise `AttributeError`. The rubric doesn't test this. Per "trust internal code, validate at edges" (CLAUDE.md), the worker is internal — but `store.all()` is the trust boundary from the worker's POV. Risk is low; flag for follow-up.
- **`store.all()` raising.** The rubric tests the happy path of `store.all()`. If `RouterStore.all()` raises (e.g., disk unavailable), §H2 covers the *CLI*-level fail-open but not whether the worker emits an event before the raise propagates. The author considered adding "if `store.all()` raises, the worker emits `dream.error` before re-raising" and rejected it — the CLI already emits `daydream.dream_all_error` per PR5 §H 52, and double-emission is noise.

**2. Where is this rubric aligned to the dispatcher's framing rather than to the artifact's truth conditions?**

- **The dispatch proposed `dream.*` event prefix; PR5 §H uses `daydream.dream_all_*` prefix.** The rubric author chose `dream.summary` as the *single* event name (not a prefix family), matching the `schema` envelope discriminator and keeping continuity with the existing `daydream.dream_all_skipped` / `daydream.dream_all_error` family at the CLI layer. This is a deliberate divergence from the dispatch's "dream.* prefix" framing — see §J pushback 4. If the dispatcher prefers strict `dream.*` family, criterion I2 needs to be re-pinned.
- **The dispatch said "treat ADR-002's four-job list as the worker contract."** ADR-002 is marked Accepted with `Contract: yes` but its full contract text was not read for this rubric (only the `Contract` line was confirmed). If ADR-002 names a specific return-dict schema that conflicts with §B, §B loses. The rubric author flags this as a research debt: re-read ADR-002's body before locking the §B literals.
- **The rubric did not come from the artifact's edits.** This is a first-pass rubric; no FAIL→PASS transitions exist yet. The risk this question targets (rubric drift across review rounds) is not present today.

### Findings

- `RUBRIC_GAP: cluster-size bound` — no upper cap on cluster cardinality; a pathological store can produce an unwieldy dict. Not a blocker for v1; surface for follow-up ADR or rubric revision when the first large-fixture bench lands.
- `RUBRIC_GAP: normalized_key value not pinned` — the *form* of the key string is unconstrained, leaving room for silent algorithm drift. Acceptable for v1 (no downstream consumer of the key yet); pin in the follow-up rubric that adds the mutation half.
- `RUBRIC_GAP: store.all() raise path untested at worker layer` — covered at the CLI layer by §H, intentionally not duplicated at the worker layer; flag for revisit if duplicate-emission diagnostics become useful.
- **ADR-002 contract text verified** (dispatcher's check). The Consequences section names dedup/contradiction/governance/retention as the four jobs and pins the CLI surface (`memory dream --all`); it does NOT prescribe a return-dict schema. §B's literals are free to stand. (Was: `RUBRIC_GAP: ADR-002 body not fully read`.)

---

## Pushbacks (from the rubric author to the dispatcher)

1. **Dict shape — `schema` envelope key added.** The dispatch listed envelope as "maybe." The rubric adds it as required (`"schema": "dream.summary"`). Rationale: an envelope discriminator lets the diary reader and future Langfuse pipeline (per ADR-009 migration note) demultiplex this dict from other event payloads without sniffing keys. The cost is one string literal; the benefit is forward-compatibility with the events stream migration. If you'd rather defer this until the events stream actually demultiplexes, drop B2 and accept that future readers will key off `mode` instead.

2. **Normalization scheme — punctuation stripping is wrong for code-content items.** "Lowercase + collapse whitespace + strip punctuation" silently merges `"x = 1"` and `"x  1"` and `"x1"` (after stripping `=` and collapsing whitespace, all three normalize to `"x 1"` or `"x1"` depending on order of operations). For QA-content stores this is fine. For SWE-Bench-CL or SWE-ContextBench stores where memories may include code snippets, this normalization will produce false-positive duplicates. The rubric pins the v1 algorithm (§E) but flags this as a known sharp edge. **Recommended posture:** ship the v1 algorithm, add §B's `mode == "detection"` field so downstream code can refuse to *act* on a detection that came from a too-aggressive normalization, and write a follow-up rubric that introduces `mode == "detection_v2"` with a code-aware normalizer (e.g., preserve `=`, `(`, `)`, `{`, `}`, `:`, `[`, `]`). If you prefer to gate v1 on a code-aware normalizer now, criterion E1–E5 need rewriting.

3. **`trajectories_path` posture — accept-and-error on truthy.** The dispatch initially leaned toward accept-and-ignore; halliday review flipped this to **accept-and-error** — silently ignoring a caller-supplied path is the precise shape of bugs that take months to diagnose ("I passed `--trajectories /foo` and nothing happened"). The rubric §G now pins accept-and-error: `None` passes through; any **truthy** value raises `ValueError`. Falsy-but-non-`None` values (`""`, `0`) are not explicitly pinned — the implementer chose truthy-rejection, matching halliday's "anything truthy" framing, so `""` passes through as if `None`. Rationale for `ValueError` (not `NotImplementedError`): the CLI catches `NotImplementedError` as the v1-stub skip path (cli.py:232-236) and would silently swallow this; `ValueError` falls through to the CLI's generic `except Exception` branch and emits `daydream.dream_all_error` — still exit-0 fail-open, but observably distinct from the skip path.

4. **Event name — `dream.summary` (singular), not `dream.*` prefix family.** The dispatch suggested a `dream.*` prefix. The CLI already emits a `daydream.dream_all_*` family for skip/error states (PR5 §H 50, 52). Adding a parallel `dream.*` family at the worker layer means two prefixes for the same logical surface, which is noise. The rubric pins exactly one worker-layer event — `dream.summary` — that mirrors the dict's `schema` envelope key. CLI-level error/skip events keep their `daydream.dream_all_*` names per §H. If you want the prefix family, I2 needs re-pinning and §K should add a non-goal stating which `dream.*` events are deferred.

5. **`--dry-run` guard is overkill for v1.** Detection-only is already a dry run by construction (§F guarantees zero mutation). Adding a `--dry-run` flag would be a UX surface that has no semantic effect, which is the shape of a bug surface. **Recommended posture:** no `--dry-run` flag in v1. When the mutation half ships, the mutation flag is the gate (e.g., `--apply`), not its inverse. Rubric does not require `--dry-run`.

6. **Contract-validation criterion the rubric deliberately omits.** "Doesn't break the bench" — unverifiable inside this scope (no bench fixture under the dreaming domain's ownership; the bench-readiness gap is the harness/eval domains' lane per the dispatch's context). The rubric does not include it. If you want it, dispatch it as a separate criterion against the eval-domain test surface.

---

## How to grade against this rubric

1. Run the §A–§L unit tests: `pytest eval/memeval/dreaming/tests/test_worker.py -v`. Every test in §A–§L corresponds to one criterion; one test failure = one criterion FAIL.
2. Run the shell-command criteria (§A4, §F5, §H3, §J1, §J2, §J3, §K5, §L1) verbatim; non-zero exit = criterion FAIL.
3. A single FAIL on any criterion = artifact is not done. No partial credit. Override is logged per Jasnah policy.
4. Re-read ADR-002's body before grading the first implementation; if its contract text conflicts with §B, §B is amended (with the original §B literals preserved in the amendment log) before grading proceeds.
