# Pipeline summary — swe_bench_cl

**Version:** vdjango_django_sequence-plugin-blank-4f03018-1 · **Sequence:** django_django_sequence · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 1
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Grader:** swebench · **git:** 4f03018

## Preflight

- `plugin_memory_round_trip_failed`: plugin synthetic memory preflight failed

| Stage | accuracy | relevancy | recency | efficiency | resolved | n | cost |
|---|---|---|---|---|---|---|---|
| plugin-blank | 0.1250 | 0.0000 | 0.0000 | 0.0000 | 6/50 | 50 | $1.9780 |

## Task grading

| Stage | resolved | graded | ungraded | reasons |
|---|---|---|---|---|
| plugin-blank | 6/50 | 48 | 2 | graded×48, get_test_directives yielded no test files from test_patch×1, official parser produced no statuses×1 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-blank | 43.0000 | 12.0000 | 0.0000 | 0.0000 | 0.0000 | 48.0000 | daydream_completed_without_writes |

## Dream consolidation

_not run_
