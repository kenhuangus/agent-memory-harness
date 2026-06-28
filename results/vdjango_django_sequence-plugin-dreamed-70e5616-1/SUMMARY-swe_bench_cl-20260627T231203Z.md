# Pipeline summary — swe_bench_cl

**Version:** vdjango_django_sequence-plugin-dreamed-70e5616-1 · **Sequence:** django_django_sequence · **Harness:** claude · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 2
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V6 · **Grader:** swebench-docker · **git:** 70e5616

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| plugin-dreamed | 0.7800 | 0.0000 | 0.0000 | 0.9010 | 39/50 | 50 | 50 | $2.7017 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| plugin-dreamed | 39/50 | 39/50 | 50 | 0 | graded×50 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| plugin-dreamed | 49.0000 | 62.0000 | 62.0000 | 66.0000 | 169.0000 | 50.0000 | — |

## Dream consolidation

- jobs: ['dedup_detection', 'dedup_merge', 'ttl_pruning', 'contradiction_resolution', 'governance'] · skipped: []
- items: 112 · duplicate clusters: 0 · items in duplicates: 0
- note: detection_and_mutation_and_pruning_and_contradiction_and_governance
