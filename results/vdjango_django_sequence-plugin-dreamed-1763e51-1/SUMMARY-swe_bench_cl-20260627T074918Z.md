# Pipeline summary — swe_bench_cl

**Version:** vdjango_django_sequence-plugin-dreamed-1763e51-1 · **Sequence:** django_django_sequence · **Harness:** cursor · **Model:** composer-2.5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V5 · **Grader:** swebench · **git:** 1763e51

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.2600 | 0.0000 | 0.0000 | 0.8866 | 13/50 | 50 | 50 | $0.0000 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| plugin-dreamed | 13/50 | 13/50 | 50 | 0 | graded×50 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 50.0000 | 50.0000 | 50.0000 | 35.0000 | 84.0000 | 50.0000 | — |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 59 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
