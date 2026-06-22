# Benchmark Dataset Schemas & Sample Data

This file is a reference for the **source datasets** behind the five benchmarks in this harness. For each benchmark it documents the source-dataset JSON schema, a truncated real example record, and how that record maps onto the harness's internal `Task` shape. It was produced by inspecting the five benchmark loaders and their bundled fixtures, so the field names and aliases below reflect exactly what the loaders accept.

## Conventions

- **`QA` vs `CODE`** — every benchmark resolves to a `Task` whose `kind` is either `QA` (answer graded directly against a gold string) or `CODE` (graded by applying a patch and running tests; `answer` is `None`).
- **Field names** — each schema line lists the **accepted aliases** the loader recognizes (separated by `/`); any one of them satisfies the field.
- **`?` / "optional"** — fields marked optional may be absent; defaults or auto-generation are noted inline.
- **remote-only / fixture-only** — where a field exists only in the remote Hugging Face rows or only in the bundled fixtures, it is called out inline in the mapping.

## Contents

1. [memoryagentbench — QA](#memoryagentbench--qa)
2. [longmemeval — QA](#longmemeval--qa)
3. [contextbench — CODE](#contextbench--code)
4. [swe_contextbench — CODE](#swe_contextbench--code)
5. [swe_bench_cl — CODE](#swe_bench_cl--code)
6. [Quick takeaways](#quick-takeaways)

---

### memoryagentbench — QA

**Source schema** (field: type, one per line; names show accepted aliases):

- `task_id` / `id` / `qid` / `example_id`: string (optional; auto-generated `mab_<i>` if missing)
- `subset`: string (optional; remote split name e.g. `EventQA`, `FactConsolidation`)
- `competency` / `ability` / `category` / `task_type`: string (optional; canonicalized to one of `accurate_retrieval` | `test_time_learning` | `long_range_understanding` | `conflict_resolution`)
- `question` / `query` / `input` / `prompt`: string
- `answer` / `gold` / `label` / `output` / `target`: string | null
- `choices` / `options`: array<string> (optional, multiple-choice)
- `sessions` / `context` / `chunks` / `history` / `haystack_sessions` / `documents`: array (each session may be a string OR a dict `{session_id, content, timestamp?, index?, role?, metadata?}`)
- `gold_memory_ids` / `evidence` / `evidence_ids` / `answer_session_ids` / `gold_chunks`: array<string> (optional)
- `timestamp` / `time` / `date`: ISO8601 string or epoch (optional)
- Remote HF rows are reshaped: each `{context, questions[], answers[], metadata}` is expanded into one flat row per question.

Example (from fixture, truncated):

```json
{"task_id": "mab_event_1", "subset": "EventQA", "competency": "EventQA", "question": "Which city did the user move to for the new job?", "answer": "Berlin", "gold_memory_ids": ["mab_s_move"], "sessions": [{"session_id": "mab_s_intro", "content": "Discussed weekend plans and a hiking trip near the lake.", "timestamp": "2023-01-05T09:00:00", "index": 0}, {"session_id": "mab_s_move", "content": "The user accepted an offer and relocated to Berlin for the new job.", "timestamp": "2023-03-12T14:30:00", "index": 1}]}
```

→ Task mapping: `sessions`←history chunks; `question`/`answer`←direct; `gold_memory_ids`←evidence ids (optional, fixture-only — real HF rows don't ship them); `competency`←subset canonicalized; kind=QA.

### longmemeval — QA

**Source schema** (field: type, one per line):

- `question_id` / `qid` / `id`: string (suffix `_abs` marks abstention)
- `question_type` / `type` / `ability` / `competency`: string (optional; e.g. `temporal-reasoning`, `knowledge-update`, `single-session-user`)
- `question` / `query`: string
- `answer` / `gold` / `expected_answer`: string | null
- `question_date` / `date` / `timestamp`: string (recency reference time)
- `haystack_sessions` / `sessions` / `context`: array<array<{role: string, content: string}>> (each element is a session = list of turn dicts)
- `haystack_dates`: array<string> (parallel to haystack_sessions; per-session timestamp)
- `haystack_session_ids`: array<string> (parallel; per-session id)
- `answer_session_ids` / `gold_memory_ids` / `evidence_session_ids` / `supporting_session_ids`: array<string> (evidence sessions; empty for abstention)
- `is_abstention` / `abstention`: bool (optional; usually implied by `_abs` suffix)

Example (from fixture, truncated):

```json
{"question_id": "lme_temporal_1", "question_type": "temporal-reasoning", "question": "Where did the user go for their summer vacation?", "answer": "Lisbon", "question_date": "2023-09-01 12:00", "haystack_session_ids": ["lme_sess_a", "lme_sess_b"], "haystack_dates": ["2023-05-10 (Wed) 08:30", "2023-07-15 (Sat) 19:20"], "answer_session_ids": ["lme_sess_b"], "haystack_sessions": [[{"role":"user","content":"I am thinking about where to travel."}], [{"role":"user","content":"We spent our summer vacation in Lisbon and loved the food."}]]}
```

→ Task mapping: `sessions`←zip(haystack_sessions, haystack_dates, haystack_session_ids) with turns flattened to `"role: content"`; `question`/`answer`←direct; `gold_memory_ids`←`answer_session_ids`; `competency`←normalized `question_type` (or `abstention`); kind=QA.

### contextbench — CODE

**Source schema** (field: type, one per line):

- `instance_id` / `id` / `original_inst_id`: string
- `repo` / `repository` / `repo_name`: string
- `repo_url`: string (optional, metadata)
- `language` / `lang`: string (e.g. `python`)
- `base_commit` / `commit` / `sha`: string
- `problem_statement` / `question` / `issue` / `prompt`: string
- `gold_context` / `gold_contexts` / `context` / `gold_spans`: array<{file: string, start_line: int, end_line: int, content: string}> OR JSON-encoded string of same
- `patch` / `gold_patch` / `solution`: string (unified diff)
- `test_patch` / `tests_patch`: string (unified diff)
- `f2p` / `FAIL_TO_PASS` / `fail_to_pass`: array<string> or JSON string
- `p2p` / `PASS_TO_PASS` / `pass_to_pass`: array<string> or JSON string
- `source`: string (optional; e.g. `Verified`, `Multi`)
- `created_at` / `timestamp` / `date`: optional

Example (from fixture, truncated):

```json
{"instance_id": "cb_astropy_1", "original_inst_id": "astropy__astropy-12345", "repo": "astropy/astropy", "repo_url": "https://github.com/astropy/astropy", "language": "python", "base_commit": "bbbb1111", "problem_statement": "Dividing a dimensionless Quantity by itself raises instead of returning 1.", "gold_context": "[{\"file\": \"astropy/units/quantity.py\", \"start_line\": 120, \"end_line\": 135, \"content\": \"def _divide(self, other): handle dimens…\"}]", "patch": "diff --git a/astropy/units/quantity.py …", "f2p": ["test_quantity.py::test_div"], "p2p": ["test_quantity.py::test_basic"], "source": "Verified"}
```

→ Task mapping: `sessions`←one Session per `gold_context` span (id=`file:start-end`, role=`system`); `question`←`problem_statement`; `answer`=None (CODE graded by patch+tests); `gold_memory_ids`←all span ids (every span is gold); `group_id`←`repo`; `competency`←`language`; kind=CODE.

### swe_contextbench — CODE

**Source schema** (field: type, one per line):

- `instance_id` / `task_id` / `id`: string
- `problem_statement` / `question` / `issue` / `prompt` / `instruction`: string
- `repo` / `repository` / `repo_name`: string
- `base_commit` / `commit` / `sha`: string
- `patch` / `gold_patch` / `solution`: string (unified diff)
- `test_patch` / `tests_patch`: string
- `fail_to_pass` / `FAIL_TO_PASS`: array<string> or JSON string
- `pass_to_pass` / `PASS_TO_PASS`: array<string> or JSON string
- `group_id` / `context_group` / `group` / `context_id`: string (shared-context group; remote-only — derived from `SWEContextBench_Relationship.parquet` mapping related→experience)
- `order` / `sequence_position`: int (optional; auto-incremented per group if absent)
- `language` / `lang` / `competency`: string (e.g. `python`)
- `hints_text` / `readme` / `context_text`: string (optional context blob)
- `sessions` / `context` / `context_sessions`: array (optional; pre-built session blob)
- `gold_memory_ids` / `context_ids`: array<string> (optional)
- `n_files`, `difficulty`, `related`, `version`, `environment_setup_commit`, `created_at`, `hints_text`: metadata

Example (from fixture, truncated):

```json
{"task_id": "scb_django_2", "instance_id": "scb_django_2", "group_id": "django_orm_ctx", "language": "python", "problem_statement": "Add an index hint to the same ORM filter path.", "repo": "example/django-fork", "base_commit": "aaaa2222", "patch": "diff --git a/orm.py b/orm.py\n@@\n+    qs = qs.using_index('idx_main')", "test_patch": "diff --git a/test_orm.py …", "fail_to_pass": ["test_orm.py::test_index"], "pass_to_pass": ["test_orm.py::test_basic", "test_orm.py::test_empty"], "context": [{"session_id": "scb_ctx_prev", "content": "Earlier in this group we guarded the queryset against None in orm.filter.", "timestamp": "2024-01-01T00:00:00"}]}
```

→ Task mapping: `sessions`←explicit `context`/`sessions` blob OR a single Session synthesized from `hints_text`/`readme`; `question`←`problem_statement`; `answer`=None; `gold_memory_ids`←`gold_memory_ids` if present (usually empty); `group_id`/`order` preserve shared-context grouping; `competency`←`language`; kind=CODE.

### swe_bench_cl — CODE

**Source schema** (field: type, one per line):

- Top-level wrapper: `{"sequences": [{id|repo|name|sequence_id: string, tasks: array<TaskEntry>}]}` — OR a bare list of such sequences — OR already-flat list of instances.
- TaskEntry (HF nested shape): `{metadata: {...}, task: {...}, evaluation: {...}, continual_learning: {...}}` — these four sub-dicts are merged up to a flat row before parsing.
- After flatten, expected fields per task:
  - `instance_id` / `task_id` / `id`: string
  - `problem_statement` / `question` / `issue` / `text`: string
  - `repo` / `repository`: string
  - `base_commit` / `commit`: string
  - `patch` / `gold_patch` / `solution_patch`: string (unified diff)
  - `test_patch` / `tests_patch`: string
  - `fail_to_pass` / `FAIL_TO_PASS`: array<string> or JSON string
  - `pass_to_pass` / `PASS_TO_PASS`: array<string> or JSON string
  - `sequence_position` / `order`: int (chronological position within sequence)
  - `sequence` / `group_id` / `sequence_id`: string (sequence/repo id; auto-set from parent sequence)
  - `language` / `competency`: string (defaults to `continual_learning`)
  - `created_at`, `difficulty`, `version`: metadata

Example (from fixture, truncated):

```json
{"instance_id": "astropy__astropy-1002", "task_id": "astropy__astropy-1002", "sequence": "astropy_seq", "group_id": "astropy_seq", "order": 1, "position": 1, "repo": "astropy/astropy", "base_commit": "ccc02222", "problem_statement": "Follow-up: dimensionless units should also format cleanly.", "patch": "diff --git a/units.py b/units.py\n@@\n+    return '' if q.is_dimensionless else str(q.unit)", "fail_to_pass": ["test_units.py::test_format"], "pass_to_pass": ["test_units.py::test_basic", "test_units.py::test_dimensionless"], "language": "python"}
```

→ Task mapping: `sessions`←synthesized from all prior issues in the same sequence (`order < this.order`), content = `"<prior_problem>\n\n[solution]\n<prior_patch>"`; `question`←`problem_statement`; `answer`=None; `gold_memory_ids`←ids of all those prior-issue sessions (what an ideal CL agent carries forward); `group_id`=sequence id, `order`=chronological position; kind=CODE.

### Quick takeaways

- QA benches (memoryagentbench, longmemeval): `sessions` ≈ chat haystack; `gold_memory_ids` mark the supporting session(s) for recall metrics.
- CODE benches differ in what serves as "memory": `contextbench` → code spans the gold patch needs (in-task retrieval); `swe_contextbench` → sibling/related issues in the same group; `swe_bench_cl` → the agent's own prior solved issues in the sequence (continual learning).
