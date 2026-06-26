# Pipeline summary — swe_bench_cl

**Version:** vsympy_sympy_sequence-plugin-dreamed-8c48b84-1 · **Sequence:** sympy_sympy_sequence · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Grader:** swebench · **git:** 8c48b84

| Stage | accuracy | relevancy | recency | efficiency | resolved | n | cost |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.6122 | 0.0000 | 0.0000 | 0.2806 | 30/50 | 50 | $4.7297 |

## Task grading

| Stage | resolved | graded | ungraded | reasons |
|---|---|---|---|---|
| plugin-dreamed | 30/50 | 49 | 1 | graded×49, official parser produced no statuses×1 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 49.0000 | 2.0000 | 2.0000 | 63.0000 | 158.0000 | 49.0000 | — |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 95 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
