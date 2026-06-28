# Pipeline summary — swe_bench_cl

**Version:** vsympy_sympy_sequence-plugin-dreamed-23e1865-2 · **Sequence:** sympy_sympy_sequence · **Harness:** claude · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V5 · **Grader:** swebench · **git:** 23e1865

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.6939 | 0.0000 | 0.0000 | 0.1415 | 34/49 | 49 | 50 | $4.0092 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| plugin-dreamed | 34/49 | 34/50 | 49 | 1 | graded×49, official parser produced no statuses×1 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 36.0000 | 32.0000 | 32.0000 | 20.0000 | 88.0000 | 49.0000 | partial_grading |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 75 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
