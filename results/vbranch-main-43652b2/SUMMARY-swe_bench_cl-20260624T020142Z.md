# Pipeline summary — swe_bench_cl

**Version:** vbranch-main-43652b2 · **Sequence:** pytest-dev_pytest_sequence · **Model:** claude-haiku-4-5 · **Tasks:** 5 · **Stages:** 5
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Grader:** auto · **git:** 43652b2

| Stage | accuracy | relevancy | recency | efficiency | resolved | n | cost |
|---|---|---|---|---|---|---|---|
| plugin-blank | 0.6667 | 0.0000 | 0.0000 | 0.0000 | 2/5 | 5 | $0.2554 |
| plugin-accum | 0.2500 | 0.0000 | 0.0000 | 0.0000 | 1/5 | 5 | $0.1562 |
| plugin-dreamed | 1.0000 | 0.0000 | 0.0000 | 0.0000 | 3/5 | 5 | $0.1602 |

## Task grading

| Stage | resolved | graded | ungraded | reasons |
|---|---|---|---|---|
| plugin-blank | 2/5 | 3 | 2 | graded×3, gold_test_apply_failed×2 |
| plugin-accum | 1/5 | 4 | 1 | graded×4, gold_test_apply_failed×1 |
| plugin-dreamed | 3/5 | 3 | 2 | graded×3, gold_test_apply_failed×2 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-blank | 4.0000 | 3.0000 | 0.0000 | 0.0000 | 0.0000 | 3.0000 | daydream_completed_without_writes |
| plugin-accum | 5.0000 | 4.0000 | 0.0000 | 0.0000 | 0.0000 | 4.0000 | memory_store_empty, daydream_completed_without_writes |
| plugin-dreamed | 5.0000 | 4.0000 | 0.0000 | 0.0000 | 0.0000 | 3.0000 | memory_store_empty, daydream_completed_without_writes |

## Deltas

| Transition | accuracy | relevancy | recency | efficiency |
|---|---|---|---|---|
| base_to_blank | — | — | — | — |
| blank_to_accum | -0.4167 | +0.0000 | +0.0000 | +0.0000 |
| accum_to_dreamed | +0.7500 | +0.0000 | +0.0000 | +0.0000 |
| base_to_final | — | — | — | — |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 0 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
