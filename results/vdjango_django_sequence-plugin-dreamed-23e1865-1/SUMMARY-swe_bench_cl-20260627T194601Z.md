# Pipeline summary — swe_bench_cl

**Version:** vdjango_django_sequence-plugin-dreamed-23e1865-1 · **Sequence:** django_django_sequence · **Harness:** cursor · **Model:** composer-2.5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V5 · **Grader:** swebench · **git:** 23e1865

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.2400 | 0.0000 | 0.0000 | 0.1887 | 12/50 | 50 | 50 | $0.0000 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| plugin-dreamed | 12/50 | 12/50 | 50 | 0 | graded×50 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 50.0000 | 50.0000 | 50.0000 | 20.0000 | 112.0000 | 50.0000 | — |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 102 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
