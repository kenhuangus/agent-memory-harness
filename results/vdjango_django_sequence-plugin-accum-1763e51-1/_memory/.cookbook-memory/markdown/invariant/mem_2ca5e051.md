---
type: Invariant
title: mem_2ca5e051
description: 'When using `transaction.atomic(using=db)` in Django, `Model.save()` calls inside the block must explicitly pass `using=db` – the `using` argument to `atomic` does not propagate to model operations.'
resource: 'memeval://memory/mem_2ca5e051'
timestamp: '2026-06-26T22:27:24.097610+00:00'
x_item_id: mem_2ca5e051
x_relevancy: 0.9
x_version: 1
x_session_id: c3773406-f971-4089-acf7-354b212fdf7f
x_source: daydream
x_tokens: 49
---

When using `transaction.atomic(using=db)` in Django, `Model.save()` calls inside the block must explicitly pass `using=db` – the `using` argument to `atomic` does not propagate to model operations.
