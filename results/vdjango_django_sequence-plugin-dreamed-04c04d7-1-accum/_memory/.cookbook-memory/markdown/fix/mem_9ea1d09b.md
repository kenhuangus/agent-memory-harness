---
type: Fix
title: mem_9ea1d09b
description: 'When adding a DISTINCT flag to a Django aggregate class, set `allow_distinct = True` on the class body; the base Aggregate template and as_sql() already handle DISTINCT SQL generation.'
resource: 'memeval://memory/mem_9ea1d09b'
tags:
- django
- orm
- aggregates
- distinct
timestamp: '2026-06-27T16:05:57.908393+00:00'
x_item_id: mem_9ea1d09b
x_relevancy: 0.95
x_version: 1
x_session_id: 6349ee1d-169e-4216-b95c-827ad7c0f497
x_source: daydream
x_tokens: 46
---

When adding a DISTINCT flag to a Django aggregate class, set `allow_distinct = True` on the class body; the base Aggregate template and as_sql() already handle DISTINCT SQL generation.
