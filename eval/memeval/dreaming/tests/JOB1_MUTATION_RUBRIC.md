# JOB1_MUTATION_RUBRIC.md — `DreamingWorker.run()` mutation half

**Scope.** Job 1 of ADR-dreaming-002, **mutation half only**: extend the detection-only
`DreamingWorker.run()` (shipped in PR #88) to actually retire cluster losers via
`Router.delete()`, under a basedir-scope flock and an NFS-detection guard, per
ADR-dreaming-021. Daydream-side change (engine.daydream acquires the basedir lock before
the per-session lock) is IN scope per ADR-021 Decision 4.

**Out of scope** (explicit, do not grade against): Job 2 (contradiction resolution),
Job 3 (governance), Job 4 (pruning), the `MemoryStore.delete()` [CONTRACT] PR (the
duck-typed `Router.delete()` is the contract per ADR-021), stale-lock reclamation
(future ADR), consolidated-write-back / tombstones / CAS semantics (forbidden by
ADR-021 §Policy without a successor ADR), embeddings, trajectory reading, non-stdlib
packages, cross-platform NFS detection beyond Linux (`statvfs`/`/proc/mounts`) and
Darwin (`getattrlist`).

**Targets.**
- `eval/memeval/dreaming/worker.py` — `DreamingWorker.run` body.
- `eval/memeval/dreaming/_state.py` — adds `_basedir_dream_lock`, `_DreamLockHeld`,
  `_UnsupportedFsError`, and a monkeypatchable `_is_network_fs(path) -> bool`.
- `eval/memeval/dreaming/engine.py` — `daydream()` acquires the basedir lock before
  the per-session lock; emits `daydream.dream_in_progress_skipped` on contention.
- `eval/memeval/dreaming/cli.py` — `_handle_dream` catches `_DreamLockHeld` and
  `_UnsupportedFsError` separately from the generic `except Exception` branch.
- New unit tests under `eval/memeval/dreaming/tests/test_worker_mutation.py`,
  `eval/memeval/dreaming/tests/test_basedir_lock.py`,
  `eval/memeval/dreaming/tests/test_daydream_basedir_lock.py`.

**Supersedes.**

- `INITIAL_DREAM_RUBRIC.md` §F1, §F2, §F3, §F4, §F5, §F6 — the "no mutation"
  contract is OVERTURNED. The worker now mutates the store via `Router.delete()`.
  This rubric replaces them with mutation INVARIANTS (§F here): exactly N deletes
  occur where N = sum(cluster.count - 1 for cluster in summary.clusters); winner is
  preserved; non-clustered items are untouched.
- `INITIAL_DREAM_RUBRIC.md` §B4 (`result["mode"] == "detection"`), §B5
  (`result["jobs_run"] == ["dedup_detection"]`), §B6 (`skipped_jobs` list pinned with
  `"dedup_merge"`) — the literal values change. Replaced by §B here.
- `INITIAL_DREAM_RUBRIC.md` §J3 — the AST walk in J3 is vacuous (it queried
  `store.<attr>` but the implementation uses `self.store.<attr>`). Replaced by §J3
  here with a broadened AST walk that catches `self.store.<attr>`. **Note (post-rebase
  on PR #99):** the original rubric draft framed `self.store.delete` as a "duck-typed
  carve-out via ADR-021 §Policy." That framing is now historical — PR #99 merged
  `delete(item_id: str) -> bool` into the frozen `MemoryStore` protocol (4-owner
  sign-off, see ADR-021 Open Item: "MemoryStore.delete() [CONTRACT] PR"). The worker
  is now strictly protocol-only; no carve-out needed. The §J3 allow-set
  `{'all','get','delete'}` is the frozen protocol's mutation+read surface for Dream.
- `INITIAL_DREAM_RUBRIC.md` §K4 ("v1 does NOT merge or retire") — dropped; mutation
  is now the job. Other §K non-goals (K1, K2, K3, K5, K6, K7) are preserved.
- `INITIAL_DREAM_RUBRIC.md` §L1 ("no fcntl / flock references in worker.py") —
  superseded. The worker now acquires `_basedir_dream_lock`. §L1 here re-pins this
  by requiring the lock to come from `_state` (worker does not inline `fcntl`
  directly).

**Preserved** (NOT superseded): INITIAL_DREAM_RUBRIC.md §A (surface), §B1/B2/B3/B7/B8/B9
(dict-shape skeleton and JSON round-trip), §C (counts arithmetic, with new mutation
counter added), §D (determinism — second `run()` is a no-op on the mutated store),
§E (normalization), §G (trajectories_path), §H (CLI fail-open), §I (one
`dream.summary` event with field set extended), §J1/J2 (import allow-list +
no concrete-class names — `Router.delete` is dispatched through the store reference,
not by importing `Router`).

**Format law.** Every criterion is PASS / FAIL / N-A. No "mostly," "should,"
"approximately." Each names its verification mode — `unit test` or `shell command` —
verbatim. No compound criteria (no "and/or" in a single line; split if needed).

**Open contracts pinned in this rubric** (load-bearing decisions ADR-021 left
implementer-defined; resolved here by dispatcher acceptance of jasnah's Pushbacks):

1. **`mode` literal** = `"detection_and_mutation"` (Pushback 1, dispatcher-confirmed).
2. **Cluster schema extension** — each cluster dict gains `winner_id: str` and
   `retired_ids: list[str]` so §F invariants verify from the dict alone (Pushback 2,
   dispatcher-confirmed).
3. **Winner-selection rule** = latest `item.timestamp`; lexicographically lowest
   `item_id` as tiebreaker (Pushback 3, dispatcher-confirmed). Pinned by §D5a/D5b
   tests below.
4. **`basedir == Path(os.environ["MEMORY_STORE"])`** when `MEMORY_STORE` is set;
   otherwise the worker constructor argument. The NFS check applies to this single
   resolved path (closes RUBRIC_GAP on §L17).
5. **Worker is runtime-typed `MemoryStore + .delete`**, NOT pure `MemoryStore`
   protocol — duck-typing across the protocol is the ADR-021 §Policy-authorized
   exception for `self.store.delete` (Pushback 5).

---

## A. Surface — `run()` returns dict, mutates store

- [ ] **A1.** `DreamingWorker(store).run()` over a store with two duplicate items returns a `dict` and does not raise. **Verify:** unit test `test_run_returns_dict_after_mutation`. **Boolean check:** `isinstance(result, dict)` AND no exception.
- [ ] **A2.** `DreamingWorker(store).run()` over an empty store returns a `dict` and does not raise; `Router.delete` is called zero times. **Verify:** unit test `test_run_empty_store_no_deletes`.
- [ ] **A3.** `memeval.dreaming.worker.dream(store)` returns the same dict shape as `DreamingWorker(store).run()` (object-equal) over the same store. **Verify:** unit test `test_dream_wrapper_matches_worker_mutation`.
- [ ] **A4.** `worker.py` contains zero `raise NotImplementedError` lines anywhere within `DreamingWorker.run`. **Verify:** shell command `! grep -nE 'raise[[:space:]]+NotImplementedError' eval/memeval/dreaming/worker.py`.

## B. Dict shape — exact keys, types, JSON-serializable (mutation deltas)

Required top-level keys with types (deltas from INITIAL_DREAM_RUBRIC §B in **bold**):
- `schema: str` — fixed literal `"dream.summary"`.
- `version: int` — fixed literal `1`.
- **`mode: str` — fixed literal `"detection_and_mutation"`.**
- **`jobs_run: list[str]` — exactly `["dedup_detection", "dedup_merge"]`.**
- **`skipped_jobs: list[str]` — exactly `["contradiction_resolution", "governance", "pruning"]` (order pinned).**
- **`counts: dict[str, int]` — exactly the keys `total_items`, `duplicate_clusters`, `items_in_duplicates`, `items_retired`; values are `int`.**
- **`clusters: list[dict]` — each cluster has exactly the keys `normalized_key: str`, `item_ids: list[str]`, `count: int`, `winner_id: str`, `retired_ids: list[str]`.**

Criteria:

- [ ] **B1.** Top-level key set equals exactly `{"schema","version","mode","jobs_run","skipped_jobs","counts","clusters"}`. **Verify:** unit test `test_mutation_top_level_keys_exact`.
- [ ] **B2.** `result["schema"] == "dream.summary"` (string-equal). **Verify:** unit test `test_mutation_schema_literal`.
- [ ] **B3.** `result["version"] == 1` and `type(result["version"]) is int`. **Verify:** unit test `test_mutation_version_literal`.
- [ ] **B4.** `result["mode"] == "detection_and_mutation"`. **Verify:** unit test `test_mutation_mode_literal`.
- [ ] **B5.** `result["jobs_run"] == ["dedup_detection", "dedup_merge"]` (list-equal, order pinned). **Verify:** unit test `test_mutation_jobs_run_literal`.
- [ ] **B6.** `result["skipped_jobs"] == ["contradiction_resolution","governance","pruning"]` (list-equal, order pinned). **Verify:** unit test `test_mutation_skipped_jobs_literal`.
- [ ] **B7.** `result["counts"]` has key set exactly `{"total_items","duplicate_clusters","items_in_duplicates","items_retired"}` and every value is an `int` (not `bool`, not `float`). **Verify:** unit test `test_mutation_counts_shape`.
- [ ] **B8.** `result["clusters"]` is a `list`; every element's key set equals exactly `{"normalized_key","item_ids","count","winner_id","retired_ids"}`. **Verify:** unit test `test_mutation_cluster_element_key_set`.
- [ ] **B9.** For every cluster, `winner_id` is `str` AND is present in `item_ids` AND is NOT present in `retired_ids`. **Verify:** unit test `test_mutation_cluster_winner_in_ids_not_in_retired`.
- [ ] **B10.** For every cluster, `retired_ids` is `list[str]` AND `set(retired_ids) == set(item_ids) - {winner_id}` AND `len(retired_ids) == len(item_ids) - 1`. **Verify:** unit test `test_mutation_cluster_retired_ids_exact`.
- [ ] **B11.** The returned dict round-trips through `json.dumps`/`json.loads` and the loaded value equals the original (`==`). **Verify:** unit test `test_mutation_result_json_roundtrip`.

## C. Counts arithmetic — consistency including `items_retired`

- [ ] **C1.** `result["counts"]["total_items"]` equals `len(store.all())` measured BEFORE `run()` is invoked. The test MUST snapshot `store.all()` length before calling `run()`; measuring AFTER `run()` will FAIL this criterion because retired items are gone from `store.all()`. **Verify:** unit test `test_mutation_total_items_pre_run`. (Note: `total_items` is the pre-mutation snapshot; post-mutation count is implied by `total_items - items_retired`. See §C6 for the post-mutation invariant.)
- [ ] **C2.** `result["counts"]["duplicate_clusters"] == len(result["clusters"])`. **Verify:** unit test `test_mutation_duplicate_clusters_matches_len`.
- [ ] **C3.** `result["counts"]["items_in_duplicates"] == sum(c["count"] for c in result["clusters"])`. **Verify:** unit test `test_mutation_items_in_duplicates_matches_sum`.
- [ ] **C4.** `result["counts"]["items_retired"] == sum(c["count"] - 1 for c in result["clusters"])`. **Verify:** unit test `test_mutation_items_retired_equals_loser_sum`.
- [ ] **C5.** `result["counts"]["items_retired"] == sum(len(c["retired_ids"]) for c in result["clusters"])`. **Verify:** unit test `test_mutation_items_retired_equals_retired_ids_sum`.
- [ ] **C6.** After the run, `len(store.all()) == result["counts"]["total_items"] - result["counts"]["items_retired"]`. **Verify:** unit test `test_mutation_store_size_after_run`.
- [ ] **C7.** Every cluster has `count >= 2`. **Verify:** unit test `test_mutation_clusters_have_count_at_least_two`.

## D. Determinism / idempotence — second run is a no-op

- [ ] **D1.** Calling `DreamingWorker(store).run()` twice in sequence: the second call returns `result["counts"]["items_retired"] == 0` and `result["clusters"] == []` (the first run removed all duplicates; the second has nothing to retire). **Verify:** unit test `test_mutation_second_run_is_noop`.
- [ ] **D2.** Two consecutive `run()` calls leave the store in the same state — `{(i.item_id, i.version) for i in store.all()}` after first run equals the same set after second run. **Verify:** unit test `test_mutation_second_run_no_state_change`.
- [ ] **D3.** Within a single `run()` result, no `item_id` appears in more than one cluster's `item_ids`. **Verify:** unit test `test_mutation_no_item_id_in_two_clusters`.
- [ ] **D4.** Within a single cluster, `item_ids` contains no duplicate ids. **Verify:** unit test `test_mutation_no_duplicate_ids_within_cluster`.
- [ ] **D5.** Winner selection is deterministic: two independent runs of the worker against equivalent freshly-seeded stores produce the same `winner_id` for every cluster. **Verify:** unit test `test_mutation_winner_selection_deterministic`. The rule is pinned by D5a and D5b below.
- [ ] **D5a.** **Pinned rule — recency first.** Given a fixture of two items clustering on the same normalized key with `item_id="a"` (timestamp `T_A`) and `item_id="b"` (timestamp `T_B`) where `T_A < T_B`, `worker.run()` selects `winner_id == "b"` (the more recent item wins). **Verify:** unit test `test_mutation_winner_is_latest_timestamp`.
- [ ] **D5b.** **Pinned rule — tiebreaker.** Given a fixture of two items clustering on the same normalized key with equal `timestamp` and `item_id="a"` vs `item_id="b"` (where `"a" < "b"` lexicographically), `worker.run()` selects `winner_id == "a"` (lexicographically lowest item_id wins on ties). **Verify:** unit test `test_mutation_winner_tiebreaker_lowest_id`.

## E. Normalization correctness — preserved from INITIAL_DREAM_RUBRIC §E

- [ ] **E1.** Items with contents `"Hello, world!"` and `"hello world"` cluster together; after run, exactly one of the two `item_id`s survives in `store.all()`. **Verify:** unit test `test_mutation_punct_and_case_cluster_retires_one`.
- [ ] **E2.** Items with contents `"Hello world."` and `"Hi there"` do not cluster; both `item_id`s survive in `store.all()` after run. **Verify:** unit test `test_mutation_no_false_positive_retire`.
- [ ] **E3.** Items with contents `"foo   bar"` and `"foo bar"` cluster together; exactly one retired. **Verify:** unit test `test_mutation_whitespace_collapse_cluster`.
- [ ] **E4.** Items with contents `"  foo bar  "` and `"foo bar"` cluster together; exactly one retired. **Verify:** unit test `test_mutation_strip_edges_cluster`.
- [ ] **E5.** Three items with contents `"Hello!"`, `"hello"`, `"Hello, "` produce one cluster with `count == 3` and `items_retired == 2`; exactly one survives. **Verify:** unit test `test_mutation_three_member_cluster_retires_two`.
- [ ] **E6.** An item with `content == ""` does not raise. **Verify:** unit test `test_mutation_empty_content_does_not_raise`.
- [ ] **E7.** An item with `content is None` does not raise; the worker proceeds. **Verify:** unit test `test_mutation_none_content_does_not_raise`.

## F. Mutation contract — replaces INITIAL_DREAM_RUBRIC §F (which forbade mutation)

This section is the SUPERSEDING contract. INITIAL_DREAM_RUBRIC §F1–F6 forbade any
mutation; those criteria are REVERSED here. The mutation is bounded to losers only.

- [ ] **F1.** Across a successful `run()`, `Router.delete` is invoked exactly `result["counts"]["items_retired"]` times. **Verify:** unit test `test_mutation_router_delete_call_count_equals_items_retired` — spy/instrumented Router; assert `spy.delete.call_count == result["counts"]["items_retired"]`.
- [ ] **F2.** Every `item_id` passed to `Router.delete` during a `run()` is present in the union of `retired_ids` across all clusters of `result`. **Verify:** unit test `test_mutation_every_delete_call_targets_a_retired_id`.
- [ ] **F3.** No `winner_id` from any cluster is passed to `Router.delete` during the run. **Verify:** unit test `test_mutation_winner_never_deleted`.
- [ ] **F4.** No `item_id` from a singleton (non-clustered) item is passed to `Router.delete`. **Verify:** unit test `test_mutation_singletons_never_deleted`.
- [ ] **F5.** After the run, for every `winner_id` in `result["clusters"]`, `store.get(winner_id)` returns a non-`None` `MemoryItem` whose `content` is byte-identical to the pre-run `content`. **Verify:** unit test `test_mutation_winner_content_unchanged`.
- [ ] **F6.** After the run, for every `winner_id` in `result["clusters"]`, `store.get(winner_id).relevancy` is float-equal to its pre-run value (guards a silent "merge metadata into the winner" path). **Verify:** unit test `test_mutation_winner_relevancy_unchanged`.
- [ ] **F7.** After the run, for every `retired_id` in `result["clusters"]`, `store.get(retired_id)` returns `None` (or backend-equivalent missing sentinel; for the test surface, `InMemoryStore` returns `None`). **Verify:** unit test `test_mutation_retired_ids_absent_after_run`.
- [ ] **F8.** After the run, every singleton item (item not in any cluster) is still present in `store.all()` with byte-identical `content`, float-equal `relevancy`, and equal `version`. **Verify:** unit test `test_mutation_singletons_untouched`.
- [ ] **F9.** `worker.py` source contains zero literal occurrences of `relevancy = 0` or `relevancy=0` (guards against a soft-delete fallback that contradicts ADR-021's hard-delete contract). **Verify:** shell command `! grep -nE 'relevancy[[:space:]]*=[[:space:]]*0' eval/memeval/dreaming/worker.py`.
- [ ] **F10.** `worker.py` source contains zero literal occurrences of `tombstone` (ADR-021 §Policy forbids tombstone fields). **Verify:** shell command `! grep -nE 'tombstone' eval/memeval/dreaming/worker.py`.
- [ ] **F11.** `Router.delete` (or equivalent `store.delete`) is the only mutation primitive used by `worker.py`. The worker source contains no `store.write` calls. **Verify:** shell command `! grep -nE 'store\.write' eval/memeval/dreaming/worker.py`.
- [ ] **F12.** **Ordering — all `Router.delete` calls return BEFORE the `dream.summary` event is emitted.** `counts["items_retired"]` therefore reflects deletes that have already completed, not deletes about to be attempted. Guards against the partial-fan-out failure mode (Halliday SOFTENED #1): if `Router.delete` raises mid-fan-out, the emitted summary should not claim N deletes occurred when fewer did. This is observationally equivalent to "deletes before dict construction" because §I1 mandates the emit happens exactly once with the constructed summary — the emit therefore cannot precede the dict construction. (Jasnah final grade weakened the criterion from "dict construction" to "emit" to match the test's verification surface; the impl-side correctness is unchanged.) **Verify:** unit test `test_mutation_deletes_complete_before_summary_built` — instrument `self.store.delete` to record completion timestamps; spy the `dream.summary` emit to record its own timestamp; assert every recorded delete completion precedes the emit timestamp.

## G. `trajectories_path` — preserved from INITIAL_DREAM_RUBRIC §G

- [ ] **G1.** `DreamingWorker(store).run(trajectories_path=None)` returns the same dict shape as `DreamingWorker(store).run()` over the same fresh fixture. **Verify:** unit test `test_mutation_trajectories_path_none_no_effect`.
- [ ] **G2.** `DreamingWorker(store).run(trajectories_path="/path/that/does/not/exist")` raises `ValueError` with message substring `"trajectories_path not consumed"`. **Verify:** unit test `test_mutation_trajectories_path_truthy_raises_valueerror`.
- [ ] **G3.** No filesystem access is attempted against `trajectories_path` during the truthy-then-raise path. **Verify:** unit test `test_mutation_no_filesystem_access_to_trajectories_path` — monkeypatch `pathlib.Path.open` and `builtins.open` to a counter; call `run(trajectories_path="/bogus")` inside `pytest.raises(ValueError)`; assert counter is zero.
- [ ] **G4.** The `ValueError` from §G2 is raised BEFORE the basedir lock is acquired (the `trajectories_path` guard is the first effect of `run()`). **Verify:** unit test `test_mutation_trajectories_path_raises_before_lock` — instrument `_basedir_dream_lock` to record entry; assert it was NOT entered when `ValueError` propagates.

## H. CLI fail-open — preserved from INITIAL_DREAM_RUBRIC §H

- [ ] **H1.** `daydream-cli dream --all` exits 0 on a successful `run()` call. **Verify:** unit test `test_dream_all_exits_zero_on_mutation_success`.
- [ ] **H2.** If `DreamingWorker.run` is monkeypatched to raise `RuntimeError("boom")`, `daydream-cli dream --all` exits 0 AND emits `daydream.dream_all_error`. **Verify:** unit test `test_dream_all_failopens_on_runtime_error_mutation`.
- [ ] **H3.** The worker itself does NOT catch `Exception`, `BaseException`, or `SystemExit`. **Verify:** shell command `! grep -nE 'except[[:space:]]+(Exception|BaseException|SystemExit)[[:space:]]*[:,]' eval/memeval/dreaming/worker.py`.
- [ ] **H4.** `KeyboardInterrupt` raised inside `DreamingWorker.run` propagates out of the CLI. **Verify:** unit test `test_dream_all_does_not_swallow_keyboardinterrupt_mutation`.
- [ ] **H5.** `SystemExit` raised inside `DreamingWorker.run` propagates out of the CLI. **Verify:** unit test `test_dream_all_does_not_swallow_systemexit_mutation`.
- [ ] **H6.** `_handle_dream` in `cli.py` catches `_DreamLockHeld` specifically (separate from generic `except Exception`), emits `dream.lock_contended`, and returns 0. **Verify:** unit test `test_handle_dream_catches_dreamlockheld` — monkeypatch `worker.dream` to raise `_DreamLockHeld`; assert exit 0; assert event spy received `dream.lock_contended`.
- [ ] **H7.** `_handle_dream` in `cli.py` catches `_UnsupportedFsError` specifically, emits `dream.unsupported_fs`, and returns 0. **Verify:** unit test `test_handle_dream_catches_unsupportedfserror` — monkeypatch `worker.dream` to raise `_UnsupportedFsError`; assert exit 0; assert event spy received `dream.unsupported_fs`.

## I. Observability — `dream.summary` extended; lock/NFS events pinned

- [ ] **I1.** Exactly one call to `memeval.dreaming.events.emit("dream.summary", ...)` is made during a successful `DreamingWorker.run()`. **Verify:** unit test `test_mutation_run_emits_exactly_one_summary_event`.
- [ ] **I2.** The `dream.summary` emit-call kwargs include `mode`, `total_items`, `duplicate_clusters`, `items_retired` (named field check). **Verify:** unit test `test_mutation_emit_event_required_fields`.
- [ ] **I3.** The `dream.summary` emit-call kwarg values match the returned dict: `kwargs["mode"] == result["mode"]`, `kwargs["total_items"] == result["counts"]["total_items"]`, `kwargs["duplicate_clusters"] == result["counts"]["duplicate_clusters"]`, `kwargs["items_retired"] == result["counts"]["items_retired"]`. **Verify:** unit test `test_mutation_emit_event_values_match_summary`.
- [ ] **I4.** On basedir-lock contention, `_basedir_dream_lock` emits exactly one event with name `"dream.lock_contended"` and kwarg `basedir=str(basedir)`. **Verify:** unit test `test_basedir_lock_emits_dream_lock_contended_on_contention`.
- [ ] **I5.** On NFS detection, `_handle_dream` (CLI) emits exactly one event with name `"dream.unsupported_fs"` after catching `_UnsupportedFsError`. **Verify:** unit test `test_handle_dream_emits_unsupported_fs`.
- [ ] **I6.** On Daydream-side basedir-lock contention, `engine.daydream` emits exactly one event with name `"daydream.dream_in_progress_skipped"` and returns without mutating the store or advancing the sidecar cursor. **Verify:** unit test `test_daydream_emits_dream_in_progress_skipped_on_contention`.
- [ ] **I7.** **Daydream happy-path event surface unchanged.** On a successful Daydream pass (basedir lock acquired without contention, per-session lock acquired, normal write path completes), `engine.daydream()` emits the **same event set** it emitted before ADR-021 — no new event names appear. Guards against silent surface drift that would surprise the eval harness. **Verify:** unit test `test_daydream_happy_path_event_surface_unchanged` — capture the set of event names emitted in a successful Daydream pass; assert it equals a control set (pre-ADR-021 expectation). The control set is the event names produced by `engine.daydream()` in v1 (PR #88 era) plus whatever Daydream legitimately emitted before this rubric (no `dream.*` family additions).

## J. Public-protocol-only with authorized `Router.delete` exception

INITIAL_DREAM_RUBRIC §J3's AST walk is broadened here to match `self.store.<attr>`.
Per ADR-dreaming-021 §Policy, `Router.delete` is the AUTHORIZED mutation primitive;
the worker is permitted to call it through its store handle. No other concrete-class
reach-through is allowed.

- [ ] **J1.** `worker.py`'s import block contains only stdlib (`re`, `string`, `os`, `logging`, `pathlib` — `fcntl` is NOT permitted directly, must come via `_state`), `from ..protocols import MemoryStore`, `from ..schema import MemoryItem`, `from .events import emit`, and `from ._state import _basedir_dream_lock, _DreamLockHeld, _UnsupportedFsError, _is_network_fs`. The allow-list includes `pathlib` (required by §L17 basedir Path resolution) and `logging` (required by §L18 warning-level log on `DREAM_ALLOW_NETWORK_FS=1`). Both omissions in the first-draft rubric were caught by jasnah's final grade and amended here. **Verify:** shell command `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); from_imports=[(n.level,n.module) for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]; bare=[a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]; allowed_from={(0,'typing'),(0,'__future__'),(0,'json'),(0,'os'),(0,'pathlib'),(0,'logging'),(2,'protocols'),(2,'schema'),(1,'events'),(1,'_state')}; allowed_bare={'re','string','json','os','logging','pathlib'}; assert all(f in allowed_from for f in from_imports), from_imports; assert all(b in allowed_bare for b in bare), bare; print('OK')"`
- [ ] **J2.** `worker.py` contains zero references to concrete backend class names: `MarkdownStore`, `InMemoryStore`, `VectorStore`, `GraphStore`. (`Router` and `RouterStore` are NOT named — the worker calls `self.store.delete(...)` via duck-typing; the dispatch happens at the store handle's level, not by importing `Router`.) **Verify:** shell command `! grep -nE '(MarkdownStore|InMemoryStore|VectorStore|GraphStore)' eval/memeval/dreaming/worker.py`.
- [ ] **J3.** Broadened AST walk: `DreamingWorker.run` calls only `self.store.all`, `self.store.get`, and `self.store.delete` (the three methods needed; `write`/`search` MUST NOT appear). **Verify:** shell command `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); attrs=sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute) and n.value.attr=='store' and isinstance(n.value.value, ast.Name) and n.value.value.id=='self'}); print(attrs); assert set(attrs) <= {'all','get','delete'}, attrs; assert 'write' not in attrs, attrs; assert 'search' not in attrs, attrs"`. (The `self.store.delete` call is AUTHORIZED per ADR-021 §Policy even though `delete` is not in the frozen `MemoryStore` protocol; the contract is the duck-typed `Router.delete()`.)
- [ ] **J4.** `worker.py` does not import `fcntl` directly — lock acquisition happens through `_basedir_dream_lock` from `_state`. **Verify:** shell command `! grep -nE '^import[[:space:]]+fcntl|^from[[:space:]]+fcntl' eval/memeval/dreaming/worker.py`.

## K. Explicit non-goals — Job 1 mutation deliberately does NOT do

- [ ] **K1.** Job 1 mutation does NOT perform contradiction resolution. `skipped_jobs` lists `"contradiction_resolution"`. **Verify:** covered by B6.
- [ ] **K2.** Job 1 mutation does NOT build session governance. `skipped_jobs` lists `"governance"`. **Verify:** covered by B6.
- [ ] **K3.** Job 1 mutation does NOT perform selective retention or pruning. `skipped_jobs` lists `"pruning"`. **Verify:** covered by B6.
- [ ] **K4.** Job 1 mutation does NOT write a consolidated/merged item to the surviving winner — the winner's `content`, `relevancy`, and `version` are unchanged. **Verify:** §F5 + §F6.
- [ ] **K5.** Job 1 mutation does NOT introduce a tombstone field or soft-delete mechanism. **Verify:** §F9 + §F10.
- [ ] **K6.** Job 1 mutation does NOT use embeddings or semantic similarity. **Verify:** shell command `! grep -nE '(embedding|cosine|np\.|numpy|voyage|openai|anthropic)' eval/memeval/dreaming/worker.py`.
- [ ] **K7.** Job 1 mutation does NOT read trajectories. **Verify:** §G.
- [ ] **K8.** Job 1 mutation does NOT use any non-stdlib package. **Verify:** §J1.
- [ ] **K9.** Job 1 mutation does NOT implement stale-lock reclamation (a future ADR pins this). The worker does not unlink, force-clear, or steal an existing `.dream.lock`. **Verify:** shell command `! grep -nE '(unlink|os\.remove)[[:space:]]*\([^)]*\.dream\.lock' eval/memeval/dreaming/worker.py eval/memeval/dreaming/_state.py eval/memeval/dreaming/engine.py`.
- [ ] **K10.** Job 1 mutation does NOT introduce a CAS-aware or version-aware `Router.delete` variant. The worker calls the single-argument `Router.delete(item_id)` shape. **Verify:** unit test `test_mutation_router_delete_called_with_single_id_arg` — spy on Router.delete; assert every call has exactly one positional arg, no kwargs.

## L. Lock acquisition + NFS detection — ADR-021 Decisions 2, 3, 4

This section is the load-bearing concurrency contract. It does NOT exist in
INITIAL_DREAM_RUBRIC (§L1 there forbade locks); it is added wholesale here.

### L-lock — `_basedir_dream_lock` shape

- [ ] **L1.** `_basedir_dream_lock(basedir)` opens (`os.open` with `O_WRONLY|O_CREAT`, mode `0o600`) and `fcntl.flock(LOCK_EX | LOCK_NB)`-locks the file `<basedir>/.dream.lock`. **Verify:** unit test `test_basedir_lock_acquires_lock_file_at_expected_path`.
- [ ] **L2.** On `BlockingIOError` from `fcntl.flock`, `_basedir_dream_lock` raises `_DreamLockHeld` (distinct class from `_LockHeld`). **Verify:** unit test `test_basedir_lock_raises_DreamLockHeld_on_contention` — open the lock from a parent process; spawn a subprocess that tries to acquire and asserts `_DreamLockHeld` raised (not `_LockHeld`).
- [ ] **L3.** `_DreamLockHeld` is a class distinct from `_LockHeld`. **Verify:** unit test `test_DreamLockHeld_distinct_from_LockHeld` — `from memeval.dreaming._state import _DreamLockHeld, _LockHeld; assert _DreamLockHeld is not _LockHeld; assert not issubclass(_DreamLockHeld, _LockHeld); assert not issubclass(_LockHeld, _DreamLockHeld)`.
- [ ] **L4.** On contention, `_basedir_dream_lock` emits exactly one `"dream.lock_contended"` event before raising. **Verify:** unit test `test_basedir_lock_emits_event_before_raising`.
- [ ] **L5.** On normal exit from the `with` block, `_basedir_dream_lock` releases the flock (`fcntl.LOCK_UN`) and closes the fd. **Verify:** unit test `test_basedir_lock_releases_on_normal_exit` — acquire, exit, then a fresh acquisition from the same process succeeds.
- [ ] **L6.** On exception inside the `with` block, `_basedir_dream_lock` releases the flock and closes the fd before propagating. **Verify:** unit test `test_basedir_lock_releases_on_exception` — acquire context, raise `RuntimeError` inside, assert `RuntimeError` propagates, assert a fresh acquisition succeeds.
- [ ] **L7.** The lock file at `<basedir>/.dream.lock` is NOT unlinked by the lock context on either path (exit or exception). **Verify:** unit test `test_basedir_lock_does_not_unlink_lock_file`.

### L-order — basedir lock BEFORE per-session lock and BEFORE any state read/mutation

This is the load-bearing ordering invariant from ADR-021 Decision 4.

- [ ] **L8.** In `engine.daydream()`, the basedir lock is acquired BEFORE the per-session lock. **Verify:** unit test `test_daydream_basedir_lock_before_per_session_lock` — instrument both `_basedir_dream_lock` and `_per_session_lock` to record acquisition order; assert basedir comes first.
- [ ] **L9.** In `engine.daydream()`, the basedir lock is acquired BEFORE any `store.all()` or `store.write()` call. **Verify:** unit test `test_daydream_basedir_lock_before_store_access` — instrument the store; assert no method calls occur before basedir lock acquisition.
- [ ] **L10.** In `engine.daydream()`, the basedir lock is acquired BEFORE any sidecar cursor mutation. **Verify:** unit test `test_daydream_basedir_lock_before_sidecar_mutation` — instrument `save_sidecar` (or equivalent); assert no writes before basedir lock acquisition.
- [ ] **L11.** In `engine.daydream()`, on basedir-lock contention (`_DreamLockHeld`), the function returns without acquiring the per-session lock, without calling `store.all()`/`store.write()`, and without mutating the sidecar cursor. **Verify:** unit test `test_daydream_on_basedir_contention_no_state_touched`.
- [ ] **L12.** In `engine.daydream()`, on basedir-lock contention, exactly one `"daydream.dream_in_progress_skipped"` event is emitted. **Verify:** unit test `test_daydream_emits_dream_in_progress_skipped_on_contention` (shared with I6).
- [ ] **L13.** In `DreamingWorker.run()`, the basedir lock is acquired BEFORE the `store.all()` call. **Verify:** unit test `test_worker_basedir_lock_before_store_all`.
- [ ] **L14.** In `DreamingWorker.run()`, the basedir lock is acquired BEFORE any `Router.delete` / `store.delete` call. **Verify:** unit test `test_worker_basedir_lock_before_delete_calls`.
- [ ] **L15.** In `DreamingWorker.run()`, on basedir-lock contention, the function raises (or causes the CLI to catch) `_DreamLockHeld` without calling `store.all()` and without calling `Router.delete`. **Verify:** unit test `test_worker_on_basedir_contention_no_state_touched`.

### L-NFS — network-filesystem detection

- [ ] **L16.** `_state.py` exposes a callable `_is_network_fs(path: Path) -> bool` that is monkeypatchable from tests. **Verify:** unit test `test_is_network_fs_callable_exists` — `from memeval.dreaming._state import _is_network_fs; assert callable(_is_network_fs)`.
- [ ] **L17.** When `_is_network_fs` returns `True` and `DREAM_ALLOW_NETWORK_FS` is unset (or not `"1"`), `DreamingWorker.run()` raises `_UnsupportedFsError` BEFORE acquiring the basedir lock and BEFORE calling `store.all()`. The path passed to `_is_network_fs` is the single resolved basedir per the preamble's Open-contracts pin #4: `Path(os.environ["MEMORY_STORE"])` when `MEMORY_STORE` is set, otherwise the worker constructor's basedir argument. **Verify:** unit test `test_worker_raises_unsupported_fs_on_network_fs` — monkeypatch `_is_network_fs` to return `True`; instrument lock + store; assert `_UnsupportedFsError`; assert no lock acquired and no store call; assert the path passed to `_is_network_fs` equals the resolved basedir.
- [ ] **L18.** When `_is_network_fs` returns `True` and `DREAM_ALLOW_NETWORK_FS == "1"`, `DreamingWorker.run()` proceeds (no `_UnsupportedFsError`) AND a warning-level log entry is emitted naming the basedir. **Verify:** unit test `test_worker_proceeds_with_dream_allow_network_fs_env`.
- [ ] **L19.** `_UnsupportedFsError` is a class distinct from `_DreamLockHeld` and `_LockHeld`. **Verify:** unit test `test_UnsupportedFsError_distinct` — `from memeval.dreaming._state import _UnsupportedFsError, _DreamLockHeld, _LockHeld; assert _UnsupportedFsError is not _DreamLockHeld; assert _UnsupportedFsError is not _LockHeld`.
- [ ] **L20.** `_is_network_fs` is invoked on a platform-appropriate path: on Linux, uses `statvfs` or reads `/proc/mounts`; on Darwin, uses `getattrlist`. On other platforms, logs a warning and returns `False` (fail-open detection — false-positive hard-fail is preferable to silent permit per ADR-021 Decision 3, BUT unknown platforms cannot false-positive). **Verify:** unit test `test_is_network_fs_platform_dispatch` — patch `sys.platform` to `"linux"`, `"darwin"`, `"win32"`; assert each path is exercised; on `"win32"` assert returns `False` and a warning is logged.

## M. Concurrency / cross-session correctness

- [ ] **M1.** Two `DreamingWorker.run()` invocations against the same basedir from two threads: exactly one acquires the basedir lock, the other raises `_DreamLockHeld` (or returns successfully but with no `Router.delete` calls in the losing thread). **Verify:** unit test `test_two_concurrent_workers_only_one_mutates`.
- [ ] **M2.** A `daydream-cli daydream` invocation while a `dream` worker is mid-run: Daydream catches contention, emits `daydream.dream_in_progress_skipped`, returns 0, and does NOT advance its sidecar cursor. **Verify:** unit test `test_daydream_skips_while_dream_running`.
- [ ] **M3.** Two `DreamingWorker.run()` invocations against the same in-memory store (single-process, serial): the second call returns `items_retired == 0` and the store ends in the same state as after the first call. **Verify:** covered by D1 + D2.
- [ ] **M4.** `worker.py` does not contain any direct `fcntl.flock`/`fcntl.LOCK_*` references (all lock primitives are accessed via `_state._basedir_dream_lock`). **Verify:** shell command `! grep -nE '(fcntl\.flock|fcntl\.LOCK_)' eval/memeval/dreaming/worker.py`.

---

## Rubric Adversarial Pass

**1. What does this rubric miss?**

- **Winner-selection rule is implementer-defined.** §D5 requires winner selection to be *deterministic* but does not pin the rule (latest timestamp vs. lowest item_id vs. first-seen). A future bench that assumes "earliest item wins" will silently disagree with one that assumes "latest item wins." The author did not pin the rule because ADR-021 doesn't pin it, and pinning here would create a contract that supersedes the ADR. Surfaced as Pushback 3 — dispatcher must choose.
- **`Router.delete` failure modes untested at worker layer.** If one of the three backends raises mid-fan-out (e.g., disk full on markdown backend), `Router.delete` returns whatever count it managed; the worker continues. The rubric does not test: (a) whether `items_retired` reflects backend-actual deletions or attempted deletions, (b) whether a partial-fan-out leaves the system in an observably-inconsistent state. ADR-021 says fan-out is unconditional and the return is the count that succeeded. The rubric treats `items_retired` as attempted (= losers identified), not actual (= sum of `Router.delete` return values). This is an inconsistency that surfaces only on backend failure.
- **NFS detection on Linux distinguishes basedir from `$MEMORY_STORE`.** ADR-021 Decision 3 says "$MEMORY_STORE is on NFS" — but the rubric checks `_is_network_fs(basedir)`. If `$MEMORY_STORE` and basedir resolve to different paths (they should not, but the rubric does not pin them as equal), the check covers the wrong path. Not a blocker; flag for follow-up if a real divergence appears.
- **Stale-lock reclamation explicitly out of scope (K9), but the symptom is not.** A crashed Dream worker leaves `.dream.lock` flock-held until kernel reaps the fd on process death. flock IS fd-bound and DOES release on process death (per ADR-014 halliday F3 note), so the symptom should not manifest. The rubric does not test crash-survival explicitly. If the implementer uses `fcntl.lockf` instead of `fcntl.flock`, the lock survives process death and the system wedges. §L1 pins `fcntl.flock` literally — that closes this gap.
- **Daydream-side contract: which events fire on which orderings?** §L11+§L12 + §I6 cover the contention path. But the rubric does not test "Daydream acquires basedir lock successfully (no Dream running)" — does Daydream still emit anything new in that case? ADR-021 doesn't say, but a silent change to Daydream's event surface would surprise the eval harness. Author judged this acceptable: Daydream's existing event surface is preserved unchanged.
- **JSON serializability is checked (B11) but the deletions are not transactional across the JSON write.** If the dict is built before `Router.delete` runs, the dict can claim `items_retired=N` even if Router.delete crashes before completing. §F1 asserts the call count equals `items_retired`, which only holds if the dict is built AFTER deletes complete. Implementer should build the summary dict in a sequence: (1) cluster, (2) delete losers, (3) construct dict, (4) emit + return. The rubric does not pin this ordering explicitly. Flag.

**2. Where is this rubric aligned to the dispatcher's framing rather than to the artifact's truth conditions?**

- **`mode` literal `"detection_and_mutation"` came from the research bundle, not from ADR-021.** ADR-021 does not pin the mode-string value. The author chose the descriptive name; if the dispatcher prefers `"mutation"`, B4 needs re-pinning. Surfaced as Pushback 1.
- **`winner_id` and `retired_ids` cluster fields came from the research bundle, not from ADR-021.** ADR-021 says nothing about the summary dict's shape. The author added these fields because they make §F2/F3/F8 verifiable without instrumentation. If the dispatcher wants a minimal dict (only `normalized_key`/`item_ids`/`count` as in v1), §B and §C drop those fields and §F suite must rely on Router.delete spies instead. Surfaced as Pushback 2.
- **Daydream-side change bundled into this PR.** ADR-021 Decision 4 names this as part of the mutation contract. The dispatcher's task description confirms it. The author proceeded; if the dispatcher wants Daydream-side as a follow-up PR (smaller scope per PR), §L8–L12 and §I6 move out and a successor rubric covers them. Surfaced as Pushback 4.
- **Rubric came from the same source as the artifact-to-be.** The artifact does not exist yet (this is a pre-implementation rubric). No FAIL→PASS transitions across rounds; the drift risk is not present today. Re-evaluate after first review round.

### Findings (status updated after dispatcher resolution)

- `CLOSED: items_retired semantics — attempted vs. actual` — closed in practice by §F12 (deletes complete before summary), which makes the divergence observable: `items_retired` reflects what `Router.delete` returned, not what the worker hoped to delete. Backend-failure-driven divergence will surface as a §C5 (`items_retired == sum(len(retired_ids))`) test FAIL, not a silent data drift.
- `CLOSED: winner-selection rule not pinned` — closed by preamble Open-contracts pin #3 + §D5a + §D5b (latest timestamp, lexicographically lowest item_id tiebreak).
- `RUBRIC_GAP: Router.delete partial-fan-out untested` — still open. What happens if 2 of 3 backends accept the delete and 1 raises? Not tested. ADR-021 says return is the success count; the worker's contract for that partial-success case is undefined. Acceptable for v1 because §F12 makes divergence observable; surface after first real backend-failure incident.
- `CLOSED: NFS detection path scope` — closed by preamble Open-contracts pin #4 + §L17 amendment (basedir == `Path(os.environ["MEMORY_STORE"])` when set, else worker constructor arg).
- `CLOSED: dict-construction ordering not pinned` — closed by §F12.
- `CLOSED: Daydream happy-path event surface drift` — closed by §I7.

---

## Pushbacks (from the rubric author to the dispatcher)

1. **`mode` literal.** I pinned `"detection_and_mutation"` because it's accurate and reads cleanly in a `dream.summary` envelope. The research bundle floated both this and `"mutation"`. ADR-021 pins neither. Recommended posture: keep `"detection_and_mutation"` because the worker still does detection then mutation in the same call; `"mutation"` would suggest detection is no longer happening, which is false. If you prefer `"mutation"`, B4 needs re-pinning.

2. **`winner_id` + `retired_ids` cluster fields.** These extend the v1 cluster schema (which had only `normalized_key`/`item_ids`/`count`). I added them because they make §F2/F3/F8 verifiable from the returned dict alone, without test-only Router instrumentation. The cost is a richer dict shape that the bench/diary readers will see. The benefit is debuggability — a CLI reader can see which item won and which lost without re-deriving from logs. Recommended: keep. If you'd rather minimize the dict, drop B9/B10 + the cluster-field requirements; §F suite then relies entirely on the Router.delete spy.

3. **Winner-selection rule.** §D5 requires determinism but does not pin the rule. ADR-021 doesn't pin it either. Options: (a) latest `item.timestamp` (most recent observation wins — common dedup heuristic, matches the "freshness" intuition); (b) lowest `item_id` lexicographically (deterministic on its own, but disconnects from observation order); (c) first-seen by `store.all()` iteration order (depends on store iteration determinism — risky). Recommended: latest timestamp, with `item_id` ascending as the tiebreaker for items with equal timestamp. This makes recency-of-information-wins which matches the spirit of dedup. If you pick another, §D5 needs the rule pinned explicitly.

4. **Daydream-side change bundled into this PR.** ADR-021 Decision 4 lists `engine.daydream()` acquiring the basedir lock before the per-session lock as part of the mutation contract. The dispatcher's task description names it explicitly. I included §L8–L12 + §I6 in scope. The risk is a single PR touching `worker.py` + `_state.py` + `engine.py` + `cli.py` at once — bigger blast radius, longer review. Recommended: keep bundled — splitting risks shipping a half-state where the Dream worker takes the basedir lock but Daydream doesn't yet defer to it, which means a Daydream Stop hook firing during a Dream sweep could race the store and silently re-emit memories that the Dream sweep is about to delete. The bundle closes that window atomically. If you want them split, Job 1 Mutation ships first (worker holds the lock), Daydream-side ships immediately after as a successor PR, and the interim risk is documented.

5. **Allowing `self.store.delete` through the broadened §J3 AST walk.** The frozen `MemoryStore` protocol (`protocols.py`) does NOT declare `delete`. ADR-021 §Policy pins `Router.delete()` as the authorized contract. The worker's `self.store` is typed `MemoryStore` per `__init__` annotation but at runtime is a `RouterStore` (which has `delete`). This is duck-typing across a protocol — exactly the pattern ADR-021 §Policy explicitly authorizes. The rubric (§J3) allows it. This means the worker is NOT strictly `MemoryStore`-only at runtime — it's `MemoryStore + .delete`. If you want strict protocol-only, the [CONTRACT] PR to add `delete` to the `MemoryStore` protocol must land first, and ADR-021 §Policy must be amended (or a successor ADR). Recommended: keep the duck-typed exception per ADR-021; do not bundle the [CONTRACT] PR into this rubric.

6. **NFS detection on unknown platforms returns `False`, not `True`.** ADR-021 Decision 3 says "false-positive hard-fail is preferable to false-negative silent-lock-but-not-enforced." A pure reading of that line would suggest unknown platforms hard-fail. But hard-failing on every Windows/BSD developer machine that runs the tests would be hostile. §L20 pins: known platforms (Linux, Darwin) detect; unknown platforms return `False` with a warning log. The "false-positive preferred over false-negative" intuition applies to *known-detector-output*, not to *unknown-platform-decision*. If you want hard-fail on unknown platforms, §L20 needs re-pinning and tests on non-Linux/Darwin CI will break.

7. **§F11 forbids `store.write` — but the worker still needs `store.all`/`store.get`/`store.delete` only.** This is consistent with the no-merge-write-back constraint (§K4). If a future change requires the worker to write back a merged item (which ADR-021 §Policy forbids without a successor ADR), §F11 and §J3 both block it. This is the intended slop fence. No action needed unless you want to weaken it.

---

## How to grade against this rubric

1. Run §A–§M unit tests: `pytest eval/memeval/dreaming/tests/test_worker_mutation.py eval/memeval/dreaming/tests/test_basedir_lock.py eval/memeval/dreaming/tests/test_daydream_basedir_lock.py -v`. One test failure = one criterion FAIL.
2. Run the shell-command criteria verbatim (§A4, §F9, §F10, §F11, §H3, §J1, §J2, §J3, §J4, §K6, §K9, §M4); non-zero exit = criterion FAIL.
3. A single FAIL = artifact is not done. No partial credit. Override is logged per Jasnah policy.
4. Adversarial pass + pushbacks must be addressed (resolved or explicitly accepted by the dispatcher) BEFORE first grading round.

