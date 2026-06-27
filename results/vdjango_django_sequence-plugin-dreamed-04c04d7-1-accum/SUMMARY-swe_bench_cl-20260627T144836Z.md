# Pipeline summary — swe_bench_cl

**Version:** vdjango_django_sequence-plugin-dreamed-04c04d7-1-accum · **Sequence:** django_django_sequence · **Harness:** claude · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V5 · **Grader:** swebench · **git:** 04c04d7

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.1224 | 0.0000 | 0.0000 | 0.0750 | 6/49 | 49 | 50 | $3.3771 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| plugin-dreamed | 6/49 | 6/50 | 49 | 1 | graded×49, official parser produced no statuses×1 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 50.0000 | 0.0000 | 0.0000 | 33.0000 | 235.0000 | 49.0000 | partial_grading |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 218 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
