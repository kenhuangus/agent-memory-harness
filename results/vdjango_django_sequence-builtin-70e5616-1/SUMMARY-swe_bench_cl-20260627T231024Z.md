# Pipeline summary — swe_bench_cl

**Version:** vdjango_django_sequence-builtin-70e5616-1 · **Sequence:** django_django_sequence · **Harness:** claude · **Model:** claude-haiku-4-5 · **Tasks:** 50 · **Stages:** 1
**Dreamer:** openrouter / deepseek/deepseek-v4-flash · **Extraction prompt:** V6 · **Grader:** swebench-docker · **git:** 70e5616

| Stage | accuracy | relevancy | recency | efficiency | resolved (graded) | graded | n | cost |
|---|---|---|---|---|---|---|---|---|
| builtin | 0.7209 | 0.0000 | 0.0000 | 0.0000 | 31/43 | 43 | 50 | $3.4708 |

## Task grading

| Stage | resolved (graded) | resolved (attempted) | graded | ungraded | reasons |
|---|---|---|---|---|---|
| builtin | 31/43 | 31/50 | 43 | 7 | graded×43, swebench Docker run did not complete×6, could not create swebench Docker test spec: HTTPSConnectionPool(host='raw.githubusercontent.com', port=443): Read timed out. (read timeout=None)×1 |

## Memory health

| Stage | recall tasks | recall events | hit events | writes | durable after | graded | warnings |
|---|---|---|---|---|---|---|---|
| builtin | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 43.0000 | partial_grading |

## Dream consolidation

_not run_
