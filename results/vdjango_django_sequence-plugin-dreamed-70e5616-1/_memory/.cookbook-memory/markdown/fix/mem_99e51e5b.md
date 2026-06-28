---
type: Fix
title: mem_99e51e5b
description: 'When a Django aggregate raises ''does not allow distinct'', add `allow_distinct = True` to the class; the base `Aggregate` already renders DISTINCT in SQL via its template.'
resource: 'memeval://memory/mem_99e51e5b'
timestamp: '2026-06-26T22:55:58.777052+00:00'
x_item_id: mem_99e51e5b
x_relevancy: 0.9
x_version: 1
x_session_id: 6cc085c5-7ee6-49b8-a64d-14719d190029
x_source: daydream
x_tokens: 42
---

When a Django aggregate raises 'does not allow distinct', add `allow_distinct = True` to the class; the base `Aggregate` already renders DISTINCT in SQL via its template.
