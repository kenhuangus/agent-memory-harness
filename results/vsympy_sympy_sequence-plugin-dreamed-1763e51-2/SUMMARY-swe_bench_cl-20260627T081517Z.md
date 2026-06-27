# Pipeline summary — swe_bench_cl

**Version:** vsympy_sympy_sequence-plugin-dreamed-1763e51-2 · **Sequence:** sympy_sympy_sequence · **Harness:** claude · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V5 · **Grader:** swebench · **git:** 1763e51

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.6800 | 0.0000 | 0.0000 | 1.8133 | 34/50 | 50 | 50 | $4.1279 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| plugin-dreamed | 34/50 | 34/50 | 50 | 0 | graded×50 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 48.0000 | 1.0000 | 1.0000 | 21.0000 | 64.0000 | 50.0000 | — |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 50 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
