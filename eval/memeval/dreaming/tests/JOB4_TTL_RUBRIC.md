# JOB4_TTL_RUBRIC.md — `DreamingWorker.run()` TTL pruning half

**Scope.** Job 4 of ADR-dreaming-002: extend the dedup-detection+mutation
`DreamingWorker.run()` (shipped in PR #98 per `JOB1_MUTATION_RUBRIC.md`) to ALSO
retire `MemoryItem` rows whose age `(now - item.timestamp)` exceeds a retention
threshold, retiring them via the same `self.store.delete()` primitive frozen into
the `MemoryStore` protocol (PR #99). Pruning runs in the SAME basedir flock and
under the SAME NFS hard-fail as Job 1. CLI surface is UNCHANGED (`daydream-cli
dream --all` already invokes the worker). Daydream side is UNCHANGED (basedir lock
acquisition order from ADR-021 Decision 4 already covers Job 4 since pruning runs
inside the same lock window).

**Out of scope** (explicit, do not grade against):

- ADR-015 filesystem-state TTL (`sweep_old_state`, the four file-class patterns:
  `*.json` sidecars, `*.daydream-events.jsonl`, `*.redact-audit.jsonl`, `*.lock`).
  Job 4 does NOT touch `sweep_old_state`. The two TTLs are orthogonal: ADR-015
  prunes filesystem state files in `<basedir>/dream/` by `mtime`; Job 4 prunes
  `MemoryItem` rows in the persistent store backend by `item.timestamp`.
- Job 2 (contradiction resolution) and Job 3 (governance).
- LRU / frequency-based / access-count-driven pruning. Job 4 is age-only.
- Per-item retention overrides (no `item.ttl` field on `MemoryItem`).
- A CAS-aware or version-aware `Router.delete` variant — `self.store.delete(item_id)`
  remains single-arg per ADR-021.
- Tombstones, soft-delete, or "merge then delete" — pruning is hard-delete.
- Multi-process stale-lock reclamation (future ADR).
- Non-stdlib packages.

**Targets.**

- `eval/memeval/dreaming/worker.py` — `DreamingWorker.run` body extended with a
  TTL-pruning pass and a `_pick_pruned(items, now, retention_seconds)` helper.
  Add `import time` to the import block; expose a module-level `_now()` seam
  whose default returns `time.time()` and which tests monkeypatch.
- New unit tests under `eval/memeval/dreaming/tests/test_worker_ttl.py`.
- `JOB1_MUTATION_RUBRIC.md` §B4/B5/B6/K3 are formally superseded by §B/§K here
  (literals + non-goals shift; the supersession is mechanical, no behavior
  reversal — Job 4 is additive, not corrective).

**Supersedes** (from `JOB1_MUTATION_RUBRIC.md`):

- `JOB1_MUTATION_RUBRIC.md` §B4 (`result["mode"] == "detection_and_mutation"`) —
  REPLACED by §B4 here pinning `"detection_and_mutation_and_pruning"`.
- `JOB1_MUTATION_RUBRIC.md` §B5 (`jobs_run == ["dedup_detection","dedup_merge"]`) —
  REPLACED by §B5 here pinning `["dedup_detection","dedup_merge","ttl_pruning"]`.
- `JOB1_MUTATION_RUBRIC.md` §B6 (`skipped_jobs == ["contradiction_resolution",
  "governance","pruning"]`) — REPLACED by §B6 here pinning
  `["contradiction_resolution","governance"]` (pruning removed; it now runs).
- `JOB1_MUTATION_RUBRIC.md` §K3 ("Job 1 mutation does NOT perform selective
  retention or pruning") — REPLACED by §K3 here: Job 4 DOES perform age-only
  retention pruning; Job 4 does NOT perform LRU/frequency-based pruning.
- `JOB1_MUTATION_RUBRIC.md` §F1 (`Router.delete invoked exactly counts.items_retired
  times`) — REPLACED by §F1 here: total `self.store.delete` calls equal
  `counts.items_retired + counts.items_pruned` (dedup losers + TTL victims).
- `JOB1_MUTATION_RUBRIC.md` §C1 (`total_items == len(store.all())` pre-run) —
  PRESERVED but reinforced by §C9 here: post-run store size equals
  `total_items - items_retired - items_pruned` (both reductions accounted for).
- `JOB1_MUTATION_RUBRIC.md` §J1 (import allow-list) — EXTENDED by §J1 here to
  include `time` (the only new stdlib import Job 4 requires).
- `JOB1_MUTATION_RUBRIC.md` §B7 (`counts` key-set) — REPLACED by §B7 here:
  `counts` key-set adds `items_pruned` and `retention_seconds_effective`.
- `JOB1_MUTATION_RUBRIC.md` §I2/I3 (`dream.summary` emit kwargs) — REPLACED by
  §I2/I3 here to also surface `items_pruned`.

**Preserved** (NOT superseded — same surface as Job 1 mutation):

- All of `JOB1_MUTATION_RUBRIC.md` §A (run returns dict, mutates store).
- `JOB1_MUTATION_RUBRIC.md` §B1/B2/B3/B8/B9/B10/B11 (top-level skeleton + JSON
  round-trip + cluster shape — dedup clusters unchanged).
- `JOB1_MUTATION_RUBRIC.md` §C2/C3/C4/C5/C6/C7 (dedup counts arithmetic).
- `JOB1_MUTATION_RUBRIC.md` §D1/D2/D3/D4/D5/D5a/D5b (dedup determinism + winner
  rule) — unchanged. The TTL pass adds its own determinism/idempotence criteria
  in §D here.
- `JOB1_MUTATION_RUBRIC.md` §E (normalization correctness — pruning does not
  affect content normalization).
- `JOB1_MUTATION_RUBRIC.md` §F2/F3/F4/F5/F6/F7/F8/F9/F10/F11/F12 (mutation
  contract — TTL adds analogues in §F here, not overrides).
- `JOB1_MUTATION_RUBRIC.md` §G (trajectories_path guard) — unchanged.
- `JOB1_MUTATION_RUBRIC.md` §H (CLI fail-open) — unchanged.
- `JOB1_MUTATION_RUBRIC.md` §I1/I4/I5/I6/I7 (single `dream.summary` emit; lock
  + NFS + Daydream events).
- `JOB1_MUTATION_RUBRIC.md` §J2/J3/J4 (no concrete classes; AST surface
  `{all, get, delete}`; no direct `fcntl`).
- ALL of `JOB1_MUTATION_RUBRIC.md` §L (basedir flock + NFS detection) — Job 4
  inherits unchanged. §L of this rubric is a one-line preservation marker.
- `JOB1_MUTATION_RUBRIC.md` §M (concurrency).
- `JOB1_MUTATION_RUBRIC.md` §K1/K2/K4/K5/K6/K7/K8/K9/K10 (non-goals other
  than K3).

**Format law.** Every criterion is PASS / FAIL / N-A. No "mostly," "should,"
"approximately." Each names its verification mode — `unit test` or `shell command`
— verbatim. No compound criteria (no "and/or" in a single line; split if needed).

**Open contracts pinned in this rubric** (load-bearing decisions ADR-002 left
implementer-defined; resolved here by dispatcher acceptance of jasnah's
Pushbacks):

1. **`mode` literal** = `"detection_and_mutation_and_pruning"` (Pushback A;
   dispatcher-confirmed). Continues the Job-1 naming convention (mode lists what
   the run actually did).
2. **`jobs_run` literal** = `["dedup_detection","dedup_merge","ttl_pruning"]`
   (order pinned; Pushback B).
3. **`skipped_jobs` literal** = `["contradiction_resolution","governance"]`
   (Pushback B; pruning removed).
4. **Env var name** = `DREAM_ITEM_RETENTION_DAYS` (Pushback C, dispatcher-confirmed).
   *Not* `DREAM_RETENTION_DAYS` — that name is ALREADY taken by ADR-015's
   `_read_ttl_days` (`_state.py:419`) for the filesystem-state file TTL. Reusing
   it would implicitly couple two orthogonal TTL surfaces (sidecar files in
   `<basedir>/dream/` vs. `MemoryItem` rows in the store backend) and force them
   to share a retention value. The dispatcher's research bundle suggested
   `MEMORY_ITEM_RETENTION_DAYS` as an alternative; this rubric pins the
   `DREAM_*`-prefixed form for surface consistency, while keeping the namespace
   distinct from `DREAM_RETENTION_DAYS`. (This pushback overrides the
   dispatcher's tentative "suggest `DREAM_RETENTION_DAYS`" framing.)
5. **Default retention** = `30` days when `DREAM_ITEM_RETENTION_DAYS` is unset.
   Matches ADR-015's default for symmetry; the two TTLs are still independent
   knobs with the same default value.
6. **TTL boundary semantics** = STRICTLY greater. `(now - item.timestamp) >
   retention_seconds` is pruned; equal is NOT pruned. This matches ADR-015's
   `mtime >= cutoff: continue` (which keeps items at exactly the cutoff;
   semantically equivalent inversion). Pinned by §F-TTL-3 below.
7. **Order of operations** = TTL pruning BEFORE dedup clustering, inside the
   same basedir lock. Rationale: (a) pruning removes stale items that would
   otherwise count toward dedup clusters and possibly become winners by recency,
   producing a counterintuitive "stale-but-just-pruned" winner state; (b) it
   reduces the working set seen by clustering; (c) it makes `items_pruned`
   independent of dedup outcomes (a pruned item cannot also be retired as a
   dedup loser within the same run because it is gone before clustering). Pinned
   by §F-TTL-2 and §D-TTL-3 below.
8. **`item.timestamp == 0.0` handling** = treated as legitimately-old and
   PRUNED (age = `now`, which is large for any real `now > retention_seconds`).
   Rationale: timestamp 0.0 is the dataclass default; in production it indicates
   either (a) a synthetic test fixture (test author's burden to set timestamp)
   or (b) a real bug in the write path. Both are better surfaced by deletion
   than by silent immortality. Pinned by §F-TTL-7.
9. **`DREAM_ITEM_RETENTION_DAYS == "0"`** = special-cased as "TTL pruning
   DISABLED" — `items_pruned == 0`, zero `self.store.delete` calls on the TTL
   path, but `jobs_run` still lists `"ttl_pruning"` (the job ran; it found
   nothing to prune). Rationale: a literal `0` retention would prune every item
   with `item.timestamp <= now`, which is every item — a footgun. The disable
   semantic matches operator intuition. Pinned by §H-TTL-2.
10. **`DREAM_ITEM_RETENTION_DAYS` negative or non-integer** = falls back to the
    30-day default with a warning log. Mirrors ADR-015's `_read_ttl_days`
    bounds-check (`_state.py:411–438`) verbatim. Pinned by §H-TTL-3 and §H-TTL-4.
11. **`now` source** = `worker._now()`, a module-level callable whose default
    returns `time.time()`. Monkeypatchable in tests. Mandatory because §D-TTL-1
    requires deterministic age computation. Pinned by §J-TTL-1.
12. **Top-level `pruned` dict** = added to the summary, parallel to `clusters`.
    Shape `{"item_ids": list[str], "retention_seconds_effective": int}`. The
    item-id list lets a CLI / bench reader see which items vanished without
    re-deriving from logs (the same debuggability argument that drove Job 1's
    `retired_ids`). Pinned by §B-TTL-1 + §B-TTL-2.
13. **MEMORY_VERSION-keyed basedir transparency** (PR #102 landed before this
    rubric): the worker is transparent to `MEMORY_VERSION`. The eval harness
    (`eval/memeval/claudecode/agent.py`) constructs `base / f"v{MEMORY_VERSION}"`
    and exports it via `$MEMORY_STORE`; `worker._resolve_basedir()` reads
    `$MEMORY_STORE` verbatim without knowledge of the version segment. Job 4
    introduces no coupling. The basedir flock and TTL pruning both operate on
    whatever `$MEMORY_STORE` resolves to — the version-keying is an upstream
    concern.

---

## A. Surface — `run()` returns dict, mutates store (Job 1 §A preserved + extended)

- [ ] **A1.** `DreamingWorker(store).run()` over a store with one item past TTL and one item fresh returns a `dict` and does not raise. **Verify:** unit test `test_run_returns_dict_after_ttl_prune`. **Boolean check:** `isinstance(result, dict)` AND no exception.
- [ ] **A2.** `DreamingWorker(store).run()` over an empty store returns a `dict` and does not raise; `self.store.delete` is called zero times. **Verify:** unit test `test_run_empty_store_no_ttl_deletes`.
- [ ] **A3.** `DreamingWorker(store).run()` over a store with no items past TTL and no dedup clusters returns `result["counts"]["items_pruned"] == 0` and `result["pruned"]["item_ids"] == []`. **Verify:** unit test `test_run_no_ttl_victims_zero_pruned`.
- [ ] **A4.** `worker.py` contains zero `raise NotImplementedError` lines (preserved from Job 1 §A4). **Verify:** shell command `! grep -nE 'raise[[:space:]]+NotImplementedError' eval/memeval/dreaming/worker.py`.

## B. Dict shape — exact keys, types, JSON-serializable (Job 4 deltas)

Required top-level keys (deltas from `JOB1_MUTATION_RUBRIC.md` §B in **bold**):
- `schema: str` — fixed literal `"dream.summary"`.
- `version: int` — fixed literal `1`.
- **`mode: str` — fixed literal `"detection_and_mutation_and_pruning"`.**
- **`jobs_run: list[str]` — exactly `["dedup_detection","dedup_merge","ttl_pruning"]`.**
- **`skipped_jobs: list[str]` — exactly `["contradiction_resolution","governance"]`.**
- **`counts: dict[str, int]` — key-set exactly `{"total_items","duplicate_clusters","items_in_duplicates","items_retired","items_pruned","retention_seconds_effective"}`; values are `int`.**
- `clusters: list[dict]` — each cluster has key-set exactly `{"normalized_key","item_ids","count","winner_id","retired_ids"}` (unchanged from Job 1 §B).
- **`pruned: dict` — key-set exactly `{"item_ids","retention_seconds_effective"}`. `pruned["item_ids"]` is `list[str]`; `pruned["retention_seconds_effective"]` is `int`.**

Criteria:

- [ ] **B1.** Top-level key set equals exactly `{"schema","version","mode","jobs_run","skipped_jobs","counts","clusters","pruned"}`. **Verify:** unit test `test_ttl_top_level_keys_exact`.
- [ ] **B2.** `result["schema"] == "dream.summary"` (string-equal). **Verify:** unit test `test_ttl_schema_literal`.
- [ ] **B3.** `result["version"] == 1` and `type(result["version"]) is int`. **Verify:** unit test `test_ttl_version_literal`.
- [ ] **B4.** `result["mode"] == "detection_and_mutation_and_pruning"`. **Verify:** unit test `test_ttl_mode_literal`.
- [ ] **B5.** `result["jobs_run"] == ["dedup_detection","dedup_merge","ttl_pruning"]` (list-equal, order pinned). **Verify:** unit test `test_ttl_jobs_run_literal`.
- [ ] **B6.** `result["skipped_jobs"] == ["contradiction_resolution","governance"]` (list-equal, order pinned). **Verify:** unit test `test_ttl_skipped_jobs_literal`.
- [ ] **B7.** `result["counts"]` key set equals exactly `{"total_items","duplicate_clusters","items_in_duplicates","items_retired","items_pruned","retention_seconds_effective"}`. **Verify:** unit test `test_ttl_counts_key_set_exact`.
- [ ] **B8.** Every `result["counts"]` value is `int`; none is `bool`; none is `float`. **Verify:** unit test `test_ttl_counts_values_are_int`.
- [ ] **B9.** `result["pruned"]` key set equals exactly `{"item_ids","retention_seconds_effective"}`. **Verify:** unit test `test_ttl_pruned_key_set_exact`.
- [ ] **B10.** `result["pruned"]["item_ids"]` is a `list`; every element is `str`. **Verify:** unit test `test_ttl_pruned_item_ids_is_list_of_str`.
- [ ] **B11.** `result["pruned"]["retention_seconds_effective"] == result["counts"]["retention_seconds_effective"]` (same number, two locations — debuggability without redundancy ambiguity). **Verify:** unit test `test_ttl_retention_seconds_consistent_across_summary`.
- [ ] **B12.** The returned dict round-trips through `json.dumps`/`json.loads` and the loaded value equals the original (`==`). **Verify:** unit test `test_ttl_result_json_roundtrip`.
- [ ] **B13.** `result["pruned"]["item_ids"]` is sorted ascending (lexicographic) — order pinned for determinism. The implementer MUST sort the pruned ids at dict-construction time (e.g., `sorted(pruned_id_set)`). Note: §F-TTL-5 / §F-TTL-2 use *completion-order* timestamps on the `self.store.delete` call stream; the dict's `pruned.item_ids` field uses *sorted-lex* order. The two orderings are independent and BOTH must hold — an impl that returns completion-ordered ids in the dict would fail §B13 at test time. **Verify:** unit test `test_ttl_pruned_item_ids_sorted_ascending`.

## C. Counts arithmetic — TTL invariants (Job 1 §C preserved; §C-TTL added)

- [ ] **C-TTL-1.** `result["counts"]["items_pruned"] == len(result["pruned"]["item_ids"])`. **Verify:** unit test `test_ttl_items_pruned_equals_len_pruned_ids`.
- [ ] **C-TTL-2.** `result["counts"]["total_items"]` equals `len(store.all())` snapshotted BEFORE `run()` is invoked. The test MUST snapshot `store.all()` length before calling `run()`. **Verify:** unit test `test_ttl_total_items_pre_run`.
- [ ] **C-TTL-3.** After the run, `len(store.all()) == result["counts"]["total_items"] - result["counts"]["items_retired"] - result["counts"]["items_pruned"]` (both reductions accounted for). **Verify:** unit test `test_ttl_store_size_after_run_accounts_for_both_paths`.
- [ ] **C-TTL-4.** `set(result["pruned"]["item_ids"])` is DISJOINT from the union of all `cluster["retired_ids"]` across `result["clusters"]`. A single item cannot be on both deletion paths in the same run (pinned by Open-contracts pin #7: TTL runs first; clustering sees only TTL survivors). **Verify:** unit test `test_ttl_pruned_disjoint_from_retired`.
- [ ] **C-TTL-5.** `set(result["pruned"]["item_ids"])` is DISJOINT from every cluster's `winner_id`. **Verify:** unit test `test_ttl_pruned_disjoint_from_winners`.
- [ ] **C-TTL-6.** `result["counts"]["retention_seconds_effective"] == effective_retention_days * 86400` where `effective_retention_days` is the value returned by the `_read_item_retention_days` helper (or equivalent inline read of `DREAM_ITEM_RETENTION_DAYS`). **Verify:** unit test `test_ttl_retention_seconds_effective_matches_env`.

## D. Determinism / idempotence — TTL deterministic on monkeypatched `now`

- [ ] **D-TTL-1.** With `worker._now` monkeypatched to a fixed value, two independent `run()` calls against equivalent freshly-seeded stores produce the same `result["pruned"]["item_ids"]` (list-equal). **Verify:** unit test `test_ttl_deterministic_under_fixed_now`.
- [ ] **D-TTL-2.** With `worker._now` monkeypatched to a fixed value, a second `run()` against the SAME (already-pruned) store returns `result["counts"]["items_pruned"] == 0` and `result["pruned"]["item_ids"] == []`. **Verify:** unit test `test_ttl_second_run_is_noop`.
- [ ] **D-TTL-3.** Within a single `run()`, TTL pruning happens BEFORE cluster computation. **Verify:** unit test `test_ttl_pruning_precedes_clustering` — instrument `self.store.delete` to record call args + an instrumentation hook on cluster computation entry; assert all TTL deletes complete before clustering begins.
- [ ] **D-TTL-4.** With `worker._now` monkeypatched, the value passed to the TTL age check equals exactly the monkeypatched value (no second call to `time.time()`). The pinned cardinality is "exactly once per **non-disabled** `run()`"; when `DREAM_ITEM_RETENTION_DAYS=0` (pin #9: TTL DISABLED), the worker may skip `_now()` entirely (call count 0 is acceptable on the disabled path). **Verify:** unit test `test_ttl_now_called_exactly_once` — instrument `worker._now`; with retention > 0, assert call count == 1 within a single `run()`.
- [ ] **D-TTL-5.** **TTL-first ordering preempts cluster formation.** Given two items that would cluster on the same normalized key, with one past TTL and one fresh: TTL pruning runs BEFORE clustering and removes the stale item, so the cluster never forms. Asserts (a) `result["pruned"]["item_ids"]` contains the stale `item_id`; (b) `result["clusters"] == []` (the surviving fresh item is a singleton); (c) `result["counts"]["items_retired"] == 0` (the dedup-loser count is 0 because clustering saw a singleton, not a pair). **Internal-consistency note (post-jasnah review):** the original "cluster-winner-past-TTL" prose was internally inconsistent — an item with a *later* timestamp than its sibling cannot also be older than retention while the sibling is fresh. What's actually load-bearing is that pin #7's TTL-first ordering prevents a stale item from being charged to the dedup `items_retired` path. **Verify:** unit test `test_ttl_preempts_cluster_winner_when_winner_is_stale`.

## E. Normalization — preserved (Job 1 §E unchanged)

- [ ] **E1.** All `JOB1_MUTATION_RUBRIC.md` §E criteria (E1–E7) hold unchanged when no items are past TTL. **Verify:** unit test `test_ttl_dedup_normalization_unchanged_when_no_prune` — re-runs Job 1 §E1–E7 fixtures with all timestamps set to `now` (so no TTL victims) and asserts identical outcomes to Job 1 baseline.

## F. Mutation contract — TTL invariants added; Job 1 §F preserved

This section ADDS TTL invariants (§F-TTL-*); Job 1 §F1 is REPLACED by §F-TTL-1
below; Job 1 §F2–F12 are preserved (mutation primitive, ordering, no winner
write-back, no soft-delete, no tombstone, all unchanged).

- [ ] **F-TTL-1.** Across a successful `run()`, `self.store.delete` is invoked exactly `result["counts"]["items_retired"] + result["counts"]["items_pruned"]` times. **Verify:** unit test `test_ttl_total_delete_call_count_equals_both_paths` — spy/instrumented store; assert `spy.delete.call_count == result["counts"]["items_retired"] + result["counts"]["items_pruned"]`.
- [ ] **F-TTL-2.** TTL deletes complete BEFORE dedup-loser deletes. **Verify:** unit test `test_ttl_deletes_complete_before_dedup_deletes` — instrument `self.store.delete` to record `(item_id, completion_timestamp)`; assert every item_id in `result["pruned"]["item_ids"]` has a completion timestamp earlier than every item_id in the union of `cluster["retired_ids"]`. **Use `time.monotonic_ns()` (or a strictly-monotonic per-call counter injected via the spy) for completion-timestamp recording — NOT `time.time()`, which has platform-dependent resolution (millisecond-granularity on some Linux distros) and could yield equal timestamps for back-to-back calls, producing a flaky result.**
- [ ] **F-TTL-3.** TTL boundary is STRICTLY greater than. An item with `(now - item.timestamp) == retention_seconds` is NOT pruned. **Verify:** unit test `test_ttl_boundary_strict_greater_than` — monkeypatch `worker._now()` to return `T`; seed an item with `timestamp == T - retention_seconds` (age exactly equal); assert this item is NOT in `result["pruned"]["item_ids"]` and IS still in `store.all()` after run.
- [ ] **F-TTL-4.** An item with `(now - item.timestamp) > retention_seconds` (one second past boundary) IS pruned. **Verify:** unit test `test_ttl_one_second_past_boundary_pruned` — monkeypatch `worker._now()` to `T`; seed item with `timestamp == T - retention_seconds - 1`; assert the item_id appears in `result["pruned"]["item_ids"]` and is absent from `store.all()` post-run.
- [ ] **F-TTL-5.** Every `item_id` passed to `self.store.delete` on the TTL path is present in `result["pruned"]["item_ids"]`. **Verify:** unit test `test_ttl_every_ttl_delete_targets_a_pruned_id` — instrument `self.store.delete`; record args; partition by ordering (TTL-path calls complete before dedup-path calls per §F-TTL-2); assert TTL-call args ⊆ `result["pruned"]["item_ids"]` and the multi-set is equal.
- [ ] **F-TTL-6.** No `item_id` whose `(now - item.timestamp) <= retention_seconds` is passed to `self.store.delete` on the TTL path. **Verify:** unit test `test_ttl_no_fresh_item_pruned`.
- [ ] **F-TTL-7.** An item with `item.timestamp == 0.0` IS pruned when `_now()` returns any value `> retention_seconds`. **Verify:** unit test `test_ttl_zero_timestamp_is_pruned`. (Open-contracts pin #8.)
- [ ] **F-TTL-8.** After the run, for every `item_id` in `result["pruned"]["item_ids"]`, `store.get(item_id)` returns `None` (or backend-equivalent missing sentinel). **Verify:** unit test `test_ttl_pruned_ids_absent_after_run`.
- [ ] **F-TTL-9.** After the run, every item NOT in `result["pruned"]["item_ids"]` AND NOT in any cluster's `retired_ids` is still present in `store.all()` with byte-identical `content`, float-equal `relevancy`, equal `version`, AND equal `timestamp`. **Verify:** unit test `test_ttl_survivors_untouched`.
- [ ] **F-TTL-10.** `worker.py` source contains zero literal occurrences of `relevancy = 0` or `relevancy=0` (preserved from Job 1 §F9; pruning is hard-delete, not relevancy-zero soft-delete). **Verify:** shell command `! grep -nE 'relevancy[[:space:]]*=[[:space:]]*0' eval/memeval/dreaming/worker.py`.
- [ ] **F-TTL-11.** `worker.py` source contains zero literal occurrences of `tombstone` (preserved from Job 1 §F10). **Verify:** shell command `! grep -nE 'tombstone' eval/memeval/dreaming/worker.py`.
- [ ] **F-TTL-12.** `worker.py` source contains zero literal occurrences of `store.write` (preserved from Job 1 §F11). **Verify:** shell command `! grep -nE 'store\.write' eval/memeval/dreaming/worker.py`.
- [ ] **F-TTL-13.** All `self.store.delete` calls (TTL path + dedup path) complete BEFORE the `dream.summary` event is emitted (extends Job 1 §F12). **Verify:** unit test `test_ttl_all_deletes_complete_before_summary_emit` — instrument `self.store.delete` to record completion timestamps; spy the `dream.summary` emit timestamp; assert every delete completion precedes the emit timestamp. **Use `time.monotonic_ns()` for completion-timestamp recording (same rationale as §F-TTL-2 — `time.time()` has platform-dependent resolution and can produce flaky ordering results).**
- [ ] **F-TTL-14.** `worker.py` source contains zero references to `MemoryItem.timestamp` mutation (no `item.timestamp =` assignment; timestamps are read-only). **Verify:** shell command `! grep -nE '\.timestamp[[:space:]]*=' eval/memeval/dreaming/worker.py`.

## G. `trajectories_path` — preserved (Job 1 §G unchanged)

- [ ] **G1.** All `JOB1_MUTATION_RUBRIC.md` §G1–G4 criteria hold unchanged. The `trajectories_path` guard remains the FIRST effect of `run()`, BEFORE NFS detection, BEFORE basedir lock, BEFORE TTL pruning. **Verify:** unit test `test_ttl_trajectories_path_truthy_raises_before_ttl_pass` — assert `ValueError` raised; assert `_basedir_dream_lock` not entered; assert `worker._now` not called.

## H. CLI fail-open + env-var ingestion

Job 1 §H1–H7 are preserved unchanged. New TTL-specific env-var criteria:

- [ ] **H-TTL-1.** When `DREAM_ITEM_RETENTION_DAYS` is unset, the worker uses `30` as the retention days; `result["counts"]["retention_seconds_effective"] == 30 * 86400 == 2_592_000`. **Verify:** unit test `test_ttl_default_retention_30_days` — monkeypatch env to ensure `DREAM_ITEM_RETENTION_DAYS` is absent; assert `result["counts"]["retention_seconds_effective"] == 2_592_000`.
- [ ] **H-TTL-2.** When `DREAM_ITEM_RETENTION_DAYS == "0"`, the TTL path is DISABLED: `result["counts"]["items_pruned"] == 0`, `result["pruned"]["item_ids"] == []`, zero `self.store.delete` calls on the TTL path, BUT `result["jobs_run"]` still contains `"ttl_pruning"`. **Verify:** unit test `test_ttl_zero_retention_disables_pruning` — seed store with items whose `timestamp == 0.0` (would normally be pruned per F-TTL-7); set env `DREAM_ITEM_RETENTION_DAYS=0`; assert `items_pruned == 0` and all seeded items still in `store.all()`.
- [ ] **H-TTL-3.** When `DREAM_ITEM_RETENTION_DAYS` is a non-integer string (e.g. `"abc"`), the worker falls back to the `30`-day default AND emits a warning-level log mentioning `DREAM_ITEM_RETENTION_DAYS`. **Verify:** unit test `test_ttl_non_integer_env_falls_back_to_default` — use `caplog`; assert `result["counts"]["retention_seconds_effective"] == 30 * 86400`; assert at least one `WARNING` record references `DREAM_ITEM_RETENTION_DAYS`.
- [ ] **H-TTL-4.** When `DREAM_ITEM_RETENTION_DAYS` is a negative integer (e.g. `"-5"`), the worker falls back to the `30`-day default AND emits a warning-level log. **Verify:** unit test `test_ttl_negative_env_falls_back_to_default`.
- [ ] **H-TTL-5.** When `DREAM_ITEM_RETENTION_DAYS == "1"`, `result["counts"]["retention_seconds_effective"] == 86400` and an item whose age is two days IS pruned. **Verify:** unit test `test_ttl_one_day_retention_prunes_two_day_old_item`.
- [ ] **H-TTL-6.** `DREAM_ITEM_RETENTION_DAYS` is read from `os.environ` on EVERY `run()` invocation (not cached at import time). **Verify:** unit test `test_ttl_env_read_per_run` — `run()` once with env unset; `run()` again with `DREAM_ITEM_RETENTION_DAYS="1"` set; assert `result["counts"]["retention_seconds_effective"]` differs between the two calls.
- [ ] **H-TTL-7.** The worker reads `DREAM_ITEM_RETENTION_DAYS`, NOT `DREAM_RETENTION_DAYS`. **Verify:** shell command `! grep -nE "DREAM_RETENTION_DAYS" eval/memeval/dreaming/worker.py` AND `grep -nqE "DREAM_ITEM_RETENTION_DAYS" eval/memeval/dreaming/worker.py`.

## I. Observability — `dream.summary` extended; lock/NFS events preserved

- [ ] **I1.** Exactly one call to `memeval.dreaming.events.emit("dream.summary", ...)` is made during a successful `DreamingWorker.run()` (preserved from Job 1 §I1). **Verify:** unit test `test_ttl_run_emits_exactly_one_summary_event`.
- [ ] **I2.** The `dream.summary` emit-call kwargs include `mode`, `total_items`, `duplicate_clusters`, `items_retired`, `items_pruned`, `retention_seconds_effective` (named field check). **Verify:** unit test `test_ttl_emit_event_required_fields_extended`.
- [ ] **I3.** The `dream.summary` emit-call kwarg values match the returned dict for ALL six required fields: `kwargs["mode"] == result["mode"]`, `kwargs["total_items"] == result["counts"]["total_items"]`, `kwargs["duplicate_clusters"] == result["counts"]["duplicate_clusters"]`, `kwargs["items_retired"] == result["counts"]["items_retired"]`, `kwargs["items_pruned"] == result["counts"]["items_pruned"]`, `kwargs["retention_seconds_effective"] == result["counts"]["retention_seconds_effective"]`. **Verify:** unit test `test_ttl_emit_event_values_match_summary_extended`.
- [ ] **I4.** Lock/NFS events preserved from Job 1: §I4 (`dream.lock_contended` on basedir-lock contention), §I5 (`dream.unsupported_fs` on NFS detection), §I6 (Daydream-side `daydream.dream_in_progress_skipped`), §I7 (Daydream happy-path event surface unchanged). **Verify:** unit tests `test_ttl_preserves_lock_contended_event`, `test_ttl_preserves_unsupported_fs_event`, `test_ttl_preserves_daydream_dream_in_progress_skipped_event`, `test_ttl_preserves_daydream_happy_path_event_surface`.
- [ ] **I5.** No new event NAME is introduced by Job 4. The event family stays at `dream.summary`, `dream.lock_contended`, `dream.unsupported_fs`, `daydream.dream_in_progress_skipped`. Per-item TTL eviction is observable from `result["pruned"]["item_ids"]`, not from a per-item `dream.item_ttl_expired` event. **Verify:** shell command `! grep -nE 'emit\([[:space:]]*["'\'']dream\.(item_ttl|item_pruned|retention_applied)' eval/memeval/dreaming/worker.py`.

## J. Public-protocol-only + import allow-list (extended for `time`)

- [ ] **J-TTL-1.** `worker.py` exposes a module-level `_now()` callable whose default returns `time.time()`. The callable is monkeypatchable from tests via `monkeypatch.setattr("memeval.dreaming.worker._now", ...)`. **Verify:** unit test `test_ttl_now_callable_exists_and_monkeypatchable` — assert `from memeval.dreaming.worker import _now; callable(_now)`; monkeypatch to a stub returning `42.0`; call `worker._now()` and assert returns `42.0`.
- [ ] **J-TTL-2.** `worker.py`'s import block contains exactly the Job 1 allow-list PLUS `time`. **Verify:** shell command `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); from_imports=[(n.level,n.module) for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]; bare=[a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]; allowed_from={(0,'typing'),(0,'__future__'),(0,'json'),(0,'os'),(0,'pathlib'),(0,'logging'),(0,'time'),(2,'protocols'),(2,'schema'),(1,'events'),(1,'_state')}; allowed_bare={'re','string','json','os','logging','pathlib','time'}; assert all(f in allowed_from for f in from_imports), from_imports; assert all(b in allowed_bare for b in bare), bare; print('OK')"`.
- [ ] **J-TTL-3.** `worker.py` does NOT import `datetime`, `dateutil`, or any timezone-handling module. TTL math is pure float subtraction. **Verify:** shell command `! grep -nE '^(import|from)[[:space:]]+(datetime|dateutil|zoneinfo|pytz)' eval/memeval/dreaming/worker.py`.
- [ ] **J-TTL-4.** Broadened AST walk: `DreamingWorker.run` calls only `self.store.all`, `self.store.get`, and `self.store.delete` (unchanged from Job 1 §J3 — Job 4 introduces no new `self.store.<attr>`). The TTL impl only requires `{all, delete}`; `get` is in the allow-set for parity with Job 1, but a Job-4 impl that calls `self.store.get` MUST justify it in an inline code comment — no production-path read goes through the worker today, so any new `self.store.get` is either dead code or a debug aid and a grader should confirm the justification. **Verify:** shell command `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); attrs=sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute) and n.value.attr=='store' and isinstance(n.value.value, ast.Name) and n.value.value.id=='self'}); print(attrs); assert set(attrs) <= {'all','get','delete'}, attrs; assert 'write' not in attrs, attrs; assert 'search' not in attrs, attrs"`.
- [ ] **J-TTL-5.** `worker.py` does not import `fcntl` directly (preserved from Job 1 §J4). **Verify:** shell command `! grep -nE '^import[[:space:]]+fcntl|^from[[:space:]]+fcntl' eval/memeval/dreaming/worker.py`.
- [ ] **J-TTL-6.** `worker.py` does not CALL `sweep_old_state` or `_read_ttl_days` (Job 4's TTL is item-level, orthogonal to ADR-015's filesystem-state TTL; no coupling). The check is **AST-based** (not literal grep) so explanatory docstring references that mirror ADR-015's naming pattern are not false positives — the no-coupling property is "the worker does not invoke those functions," not "the worker's text never contains the words." **Verify:** shell command `python3 -c "import ast; tree=ast.parse(open('eval/memeval/dreaming/worker.py').read()); refs={n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}; assert 'sweep_old_state' not in refs, refs; assert '_read_ttl_days' not in refs, refs; print('OK')"`.

## K. Explicit non-goals — Job 4 deliberately does NOT do

- [ ] **K1.** Job 4 does NOT perform contradiction resolution (`skipped_jobs` lists `"contradiction_resolution"`). **Verify:** covered by B6.
- [ ] **K2.** Job 4 does NOT build session governance (`skipped_jobs` lists `"governance"`). **Verify:** covered by B6.
- [ ] **K3.** Job 4 DOES perform age-based pruning, but does NOT perform LRU, frequency, or access-count-based pruning. **Verify:** shell command `! grep -nE '(last_accessed|access_count|frequency|hit_count|lru|LRU)' eval/memeval/dreaming/worker.py`.
- [ ] **K4.** Job 4 does NOT introduce a per-item TTL field on `MemoryItem`. **Verify:** shell command `! grep -nE '(item\.ttl|item\.expiry|\.expires_at)' eval/memeval/dreaming/worker.py`.
- [ ] **K5.** Job 4 does NOT touch ADR-015's filesystem-state TTL surface. The four file-class patterns (`*.json`, `*.daydream-events.jsonl`, `*.redact-audit.jsonl`, `*.lock`) and the `DREAM_RETENTION_DAYS` env var remain entirely controlled by `_state.sweep_old_state`. **Verify:** covered by J-TTL-6 + H-TTL-7.
- [ ] **K6.** Job 4 does NOT introduce a CAS-aware or version-aware `Router.delete` variant. The worker calls the single-argument `self.store.delete(item_id)` shape (preserved from Job 1 §K10). **Verify:** unit test `test_ttl_delete_called_with_single_id_arg` — spy on store.delete; assert every call has exactly one positional arg, no kwargs.
- [ ] **K7.** Job 4 does NOT introduce a tombstone field or soft-delete mechanism. **Verify:** covered by F-TTL-10 + F-TTL-11.
- [ ] **K8.** Job 4 does NOT use embeddings or semantic similarity. **Verify:** shell command `! grep -nE '(embedding|cosine|np\.|numpy|voyage|openai|anthropic)' eval/memeval/dreaming/worker.py`.
- [ ] **K9.** Job 4 does NOT read trajectories (preserved from Job 1 §K7). **Verify:** §G1.
- [ ] **K10.** Job 4 does NOT use any non-stdlib package (preserved from Job 1 §K8). **Verify:** §J-TTL-2.
- [ ] **K11.** Job 4 does NOT implement stale-lock reclamation (preserved from Job 1 §K9). **Verify:** shell command `! grep -nE '(unlink|os\.remove)[[:space:]]*\([^)]*\.dream\.lock' eval/memeval/dreaming/worker.py eval/memeval/dreaming/_state.py eval/memeval/dreaming/engine.py`.
- [ ] **K12.** Job 4 does NOT mutate `item.timestamp` (preserved from Job 1 §F9 spirit). Pruning is delete-only; the worker does not "refresh" an item's timestamp on access or anything else. **Verify:** covered by F-TTL-14.
- [ ] **K13.** Job 4 does NOT change the Daydream-side event surface. No `daydream.ttl_pruned` or similar. **Verify:** §I4 + §I5.
- [ ] **K14.** Job 4 does NOT introduce a per-item exemption mechanism (e.g. "pin this item; never prune"). **Verify:** shell command `! grep -nE '(pinned|exempt|never_prune|do_not_evict)' eval/memeval/dreaming/worker.py`.
- [ ] **K15.** Job 4 does NOT change the CLI surface. `daydream-cli dream --all` invokes the worker unchanged; no new flag like `--prune` or `--retention-days`. **Verify:** shell command `! grep -nE '(--prune|--retention|--ttl)' eval/memeval/dreaming/cli.py`.

## L. Lock acquisition + NFS detection — Job 1 §L preserved unchanged

- [ ] **L1.** ALL of `JOB1_MUTATION_RUBRIC.md` §L1–L20 (lock shape, lock ordering relative to store access and per-session lock, NFS detection on Linux + Darwin + unknown-platform fail-open) hold unchanged. Job 4 introduces no new lock or new NFS surface. **Verify:** unit test `test_ttl_inherits_job1_lock_and_nfs_surface` — re-run the Job 1 §L test suite (the `test_basedir_lock_*` / `test_worker_basedir_lock_*` / `test_is_network_fs_*` tests in `eval/memeval/dreaming/tests/test_worker_mutation.py`) against the Job-4-extended worker; assert all pass. (Operationally: Job 1's lock + NFS tests are co-located in `test_worker_mutation.py` — they MUST continue to pass post-Job-4.)
- [ ] **L2.** TTL pruning happens INSIDE the basedir flock — the TTL pass is between `_basedir_dream_lock` acquisition and release. **Verify:** unit test `test_ttl_prune_pass_inside_basedir_lock` — instrument `_basedir_dream_lock.__enter__` and `__exit__` to record timestamps; instrument `self.store.delete` to record TTL-path completion timestamps; assert every TTL delete completion is between lock-enter and lock-exit.
- [ ] **L3.** TTL pruning happens AFTER NFS detection (NFS hard-fail short-circuits before TTL). **Verify:** unit test `test_ttl_nfs_short_circuits_before_ttl` — monkeypatch `_is_network_fs` to return `True` with `DREAM_ALLOW_NETWORK_FS` unset; assert `_UnsupportedFsError` raised; assert `worker._now` not called; assert `self.store.delete` not called.

## M. Concurrency / cross-session correctness — Job 1 §M preserved

- [ ] **M1.** ALL of `JOB1_MUTATION_RUBRIC.md` §M1–M4 hold unchanged. Two `DreamingWorker.run()` invocations against the same basedir from two threads: exactly one acquires the basedir lock; the loser's TTL pass and dedup pass both never run. **Verify:** unit test `test_ttl_two_concurrent_workers_only_one_mutates` — extend Job 1's `test_two_concurrent_workers_only_one_mutates` to seed TTL victims; assert exactly one thread accumulates the TTL delete call count.
- [ ] **M2.** A `daydream-cli daydream` invocation while a `dream` worker is mid-TTL-pass: Daydream catches contention, emits `daydream.dream_in_progress_skipped`, returns 0, does NOT advance its sidecar cursor. **Verify:** unit test `test_daydream_skips_while_dream_ttl_running`.

---

## Rubric Adversarial Pass

**1. What does this rubric miss?**

- **`item.timestamp` provenance not verified.** The rubric pins behavior on `item.timestamp`'s value but does not verify that the value is the one set by the write path (`_extract.py:201` injects `now` into `_build_memory_item`). If a future code path mutates `item.timestamp` after the write (e.g., a "refresh on access" feature), TTL pruning silently changes meaning. K12 + F-TTL-14 forbid the worker from doing this; nothing forbids OTHER code from doing it. Flag for follow-up cross-domain audit if "access refresh" is ever proposed.
- **No test for `now < item.timestamp` (clock skew / synthetic future timestamps).** If `worker._now()` returns a value less than `item.timestamp`, `(now - item.timestamp)` is negative, which is `<= retention_seconds` for any non-negative retention, so the item is NOT pruned. This matches the strict-greater pin and is the correct conservative behavior (never prune an item that claims to be from the future). Not tested explicitly. Add a §F-TTL-15 if the dispatcher wants the case pinned.
- **Partial-fan-out failure on TTL path untested at worker layer.** Same gap as Job 1's RUBRIC_GAP on Router.delete: if `self.store.delete` raises mid-TTL-pass, the worker's behavior is unpinned. F-TTL-13 mandates all deletes complete before summary emit, which would mean the summary emit never happens on partial failure — the exception propagates to the CLI fail-open. Acceptable for v1; surface after first real backend-failure incident.
- **No test that TTL prunes BEFORE the basedir lock would re-acquire on contention.** L3 covers the NFS short-circuit case but not "what if the basedir lock is held by another process AND there are TTL victims" — the contention path raises `_DreamLockHeld` (Job 1 §L15), so no TTL deletes happen, which is correct. Implicitly covered; flag if the dispatcher wants an explicit test.
- **`pruned["item_ids"]` ordering pinned (B13) but the test does not stress duplicate-string equality of an empty list vs. `None`.** B10 + B13 together imply `[]` is the legitimate empty value; nothing forbids `None`. The JSON round-trip (B12) on a value of `None` would still succeed but B10 ("every element is `str`") would FAIL (no elements). Adequate; no fix needed.
- **The Open-contracts pin #9 (`DREAM_ITEM_RETENTION_DAYS == "0"` disables pruning) is testable but conflicts with operator intuition.** Some operators might expect `0` to mean "prune everything." H-TTL-2 pins the disable semantic, but does not test the inverse — that there is NO way to "prune everything" via env var. A footgun-protection by design; the only path to "prune everything" is to set `DREAM_ITEM_RETENTION_DAYS` to a very small positive value AND wait. Flag in §Pushbacks.

**2. Where is this rubric aligned to the dispatcher's framing rather than to the artifact's truth conditions?**

- **`mode` literal `"detection_and_mutation_and_pruning"` extrapolates the Job 1 naming convention.** ADR-002 does not pin the mode-string value. The author chose continuity with Job 1's `"detection_and_mutation"`. Surfaced as Pushback A.
- **`pruned` dict shape with `item_ids` came from the same author intuition as Job 1's `winner_id`/`retired_ids` cluster fields.** ADR-002 says nothing about the summary dict's TTL section. The author added these fields because they make F-TTL-5/F-TTL-6/F-TTL-8 verifiable from the returned dict alone (parallel to Job 1's Pushback 2). If the dispatcher wants a minimal dict (only counts, no `pruned` block), B9/B10/B11/B13 + the `pruned` key requirements drop and §F-TTL-5 must rely entirely on the `self.store.delete` spy. Surfaced as Pushback E.
- **The `DREAM_RETENTION_DAYS` → `DREAM_ITEM_RETENTION_DAYS` rename is a Pushback against the dispatcher's stated framing.** The dispatcher's task description said "suggest `DREAM_RETENTION_DAYS` (matching ADR-015's sidecar default)." But `DREAM_RETENTION_DAYS` is ALREADY a live env var read by `_state._read_ttl_days` (`_state.py:419`). Reusing the name would force the two TTL surfaces to share a value — a hidden coupling between filesystem sweep and item-store pruning. This rubric pins `DREAM_ITEM_RETENTION_DAYS` instead; the dispatcher must explicitly accept or reject (Pushback C, **critical**).
- **Order of operations (TTL before dedup) is the author's call.** The dispatcher's task description listed it as a "design decision: TTL first then dedup, dedup first then TTL, or single pass." The author pinned TTL-first by Open-contracts pin #7 and §F-TTL-2 + §D-TTL-3. Rationale documented in pin #7. Surfaced as Pushback D.
- **Rubric came from the same author as the artifact-to-be (post-implementation).** The artifact does not exist yet (this is a pre-implementation rubric). No FAIL→PASS transitions across rounds; the drift risk is not present today. Re-evaluate after first review round.

### Findings

- `RUBRIC_GAP: clock skew (now < item.timestamp) not pinned` — implicit pass via strict-greater (§F-TTL-3) means negative ages are never pruned, but no explicit test. Acceptable for v1.
- `RUBRIC_GAP: partial-fan-out on TTL path untested` — same shape as Job 1's open gap on Router.delete. Acceptable for v1; surfaces as exception-propagation through fail-open CLI.
- `RUBRIC_GAP: item.timestamp provenance not cross-checked` — Job 4 trusts `item.timestamp` as the write-time value. No test guards against a hypothetical future "access refresh" mutator. Flag for cross-domain audit if such a feature is proposed.
- `CLOSED: env var name collision (DREAM_RETENTION_DAYS already taken)` — closed by Open-contracts pin #4 + §H-TTL-7. The rubric pins `DREAM_ITEM_RETENTION_DAYS`.
- `CLOSED: ordering ambiguity (TTL vs dedup)` — closed by Open-contracts pin #7 + §F-TTL-2 + §D-TTL-3.
- `CLOSED: boundary semantics ambiguity (>=  vs >)` — closed by Open-contracts pin #6 + §F-TTL-3 + §F-TTL-4.
- `CLOSED: item.timestamp == 0.0 behavior` — closed by Open-contracts pin #8 + §F-TTL-7.
- `CLOSED: DREAM_ITEM_RETENTION_DAYS=0 semantic` — closed by Open-contracts pin #9 + §H-TTL-2.
- `CLOSED: now source` — closed by Open-contracts pin #11 + §J-TTL-1.

---

## Pushbacks (from the rubric author to the dispatcher)

**A. `mode` literal `"detection_and_mutation_and_pruning"`.** Continues the Job 1
naming convention — mode lists what the run actually did. The alternative `"full"`
or `"all_jobs"` would be shorter but ambiguous (Job 2 and Job 3 are still skipped).
Recommended: keep the verbose literal. If you prefer something else, B4 needs
re-pinning.

**B. `jobs_run` / `skipped_jobs` literal ordering.** `jobs_run` lists in execution
order: dedup_detection → dedup_merge → ttl_pruning. But per Open-contracts pin #7
+ §F-TTL-2, TTL pruning runs FIRST in clock order (so that dedup sees the
post-prune working set). The `jobs_run` list therefore reflects nominal job
identity, not execution order. If the dispatcher wants `jobs_run` to reflect
execution order, B5 should pin `["ttl_pruning","dedup_detection","dedup_merge"]`
instead. Recommended: keep the nominal-identity ordering — it matches Job 1's
convention (`["dedup_detection","dedup_merge"]` was also not execution-order; the
two are intertwined in a single sweep). Surfaced as a known semantic ambiguity.

**C. Env var name: `DREAM_ITEM_RETENTION_DAYS`, NOT `DREAM_RETENTION_DAYS` (CRITICAL).**
The dispatcher's task description suggested `DREAM_RETENTION_DAYS` "matching
ADR-015's sidecar default." That name is ALREADY taken by `_state._read_ttl_days`
(`_state.py:419`) for the filesystem-state file TTL (sidecar/lock/audit/diary in
`<basedir>/dream/`). The two TTLs are orthogonal (different file-class targets,
different timestamp sources: `mtime` for files vs. `item.timestamp` for store
rows). Reusing the env var name would silently couple them: any operator who set
`DREAM_RETENTION_DAYS=7` to prune ancient sidecar files would also prune their
memory items, and vice versa. This is a footgun. This rubric pins
`DREAM_ITEM_RETENTION_DAYS` (default 30 days, same default as ADR-015 by
coincidence, but independently configurable). Recommended: accept the rename;
update §H-TTL-* and Open-contracts pin #4. If you prefer `MEMORY_ITEM_RETENTION_DAYS`
or another name, change Open-contracts pin #4, H-TTL-3/H-TTL-4 warning messages,
and §H-TTL-7's grep pair accordingly.

**D. Order of operations: TTL pruning BEFORE dedup clustering.** Three options
were on the table per the dispatcher's design-decisions section: (a) TTL first
then dedup, (b) dedup first then TTL, (c) single pass. The author pinned (a).
Rationale: (i) dedup's winner-selection rule (latest timestamp wins, §D5a) would
otherwise pick a stale-but-just-past-TTL item as winner, then on the SAME run
prune it — wasted dedup compute and a confusing "winner pruned" state in the
summary; (ii) TTL-first reduces the working set seen by clustering; (iii) it
makes `items_pruned` independent of dedup outcomes (a pruned item cannot also be
a dedup loser because it's gone before clustering — pinned by §C-TTL-4). Cost:
TTL victims are deleted even if they would have been retired as dedup losers
anyway (the user "loses" a dedup data point — minor). Recommended: keep TTL-first.
If you want dedup-first, §F-TTL-2 inverts, §C-TTL-4 (disjointness) loses its
guarantee and would need re-pinning as "intersection is allowed and counted only
once," and §D-TTL-3 inverts.

**E. `pruned` top-level dict shape.** Parallel to Job 1's `winner_id` +
`retired_ids` cluster fields. The author added this so F-TTL-5/F-TTL-6/F-TTL-8
verify from the returned dict alone, without instrumenting `self.store.delete`.
Cost: richer dict surface that bench/diary readers will see. Benefit:
debuggability (CLI reader sees which items were pruned without re-deriving from
logs). Recommended: keep. If you'd rather minimize the dict, drop B9–B11 + B13
and §F-TTL-5 must rely entirely on the `self.store.delete` spy.

**F. Default retention = 30 days = ADR-015 default by coincidence.** The match
is intentional (one mental model for operators) but the two values are
INDEPENDENT — there is no `_read_item_retention_days(default=_read_ttl_days())`
linkage. If you want them locked together, Open-contracts pin #5 + §H-TTL-1
need re-pinning to derive the item default from the file default. Recommended:
keep them independent — one knob, one purpose, even if the defaults match today.

**G. `_now()` as a module-level seam.** Required for §D-TTL-1's determinism test
(without a monkeypatchable seam, TTL math depends on wall-clock at test run
time, which is non-deterministic). Alternative would be passing `now` as a
keyword argument to `run()`, but that would change the public surface and force
Daydream + CLI callers to also pass `now`. Module-level `_now()` is the lighter
seam. Recommended: keep. Pin #11 and §J-TTL-1 lock the shape.

**H. No new event NAME on Job 4.** §I5 forbids `dream.item_ttl_expired` or
similar per-item events. Rationale: the eval harness pinned the event family
in Job 1 §I7; adding events expands the surface and the diary writer's contract.
The `pruned` summary block carries the per-item visibility instead. Cost: high-
cardinality TTL eviction won't show in a per-item event stream. Recommended:
keep — operators wanting per-item visibility can read `result["pruned"]
["item_ids"]`. If the dispatcher wants per-item events, §I5 inverts and the
event family adds `dream.item_pruned` with kwargs `item_id`, `age_seconds`,
`retention_seconds_effective`; that change needs an explicit ADR amendment to
ADR-002 or a successor.

**I. `DREAM_ITEM_RETENTION_DAYS == "0"` disables pruning rather than "prunes
everything."** Pin #9 picks the disable semantic. The opposite reading (`0`
means "everything past timestamp `now` is pruned") would delete every item with
non-future timestamp on the first run — a footgun. Recommended: keep disable
semantic. **The disable semantic is irreversible:** there is intentionally NO
env-var path to "prune all items." An operator who genuinely wants that must
set `DREAM_ITEM_RETENTION_DAYS=1` and wait one day, or run a separate purge
tool (not provided by Job 4). This is footgun-protection by design. If the
dispatcher ever wants a one-shot purge, it requires a SEPARATE env var
(`DREAM_ITEM_RETENTION_PURGE=1`) explicitly carved out — NOT a magic-number
reading of `DREAM_ITEM_RETENTION_DAYS`. Surfaced here so a future
operator-experience PR does not silently reverse pin #9.

**J. ADR-015's `sweep_old_state` interaction.** Job 4 does NOT call
`sweep_old_state`. The filesystem-state sweep continues to be invoked by
`engine.daydream` or wherever it was previously called (verify in research:
typically end of a Daydream pass or a cron). Job 4 just adds an independent
item-row sweep inside the Dream worker. If the dispatcher wants Job 4 to ALSO
invoke `sweep_old_state` (e.g., "the Dream worker is the single TTL hook"), §J-TTL-6
inverts and a new §I-event records sweep counts. Recommended: keep them separate;
the two surfaces have different failure modes (one is filesystem, one is store
backend) and different operational owners.

---

## How to grade against this rubric

**Prerequisite.** Job 1 mutation (PR #98) is MERGED on main as of 2026-06-22; Job 4 grading inherits Job 1's lock + NFS + Daydream surface (`_state.py:_basedir_dream_lock`, `_DreamLockHeld`, `_UnsupportedFsError`, `_is_network_fs`; `engine.daydream()` basedir-lock acquisition). Job 4 grading cannot proceed if any Job 1 §L / §M / §I4 test in `test_worker_mutation.py` regresses.

1. Run §A–§M unit tests:
   `pytest eval/memeval/dreaming/tests/test_worker_ttl.py -v`
   (Job 1's existing tests in `eval/memeval/dreaming/tests/test_worker_mutation.py`
   — the `test_basedir_lock_*`, `test_worker_basedir_lock_*`, `test_daydream_basedir_lock_*`,
   `test_is_network_fs_*` test families — MUST also continue to pass; this rubric's
   §L1, §M1, and §I4 require Job 1's lock/NFS/Daydream surface unchanged.)
2. Run the shell-command criteria verbatim (§A4, §F-TTL-10, §F-TTL-11, §F-TTL-12,
   §F-TTL-14, §H-TTL-7, §I5, §J-TTL-2, §J-TTL-3, §J-TTL-5, §J-TTL-6, §K3, §K4,
   §K8, §K11, §K14, §K15); non-zero exit (or empty grep result where presence
   is required, e.g. §H-TTL-7's positive grep) = criterion FAIL.
3. A single FAIL = artifact is not done. No partial credit. Override is logged
   per Jasnah policy.
4. Adversarial pass + pushbacks must be addressed (resolved or explicitly
   accepted by the dispatcher) BEFORE first grading round. Pushback C
   (`DREAM_RETENTION_DAYS` collision) is CRITICAL — its resolution determines
   §H-TTL-1 through §H-TTL-7 and §J-TTL-6.

