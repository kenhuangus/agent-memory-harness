---
type: Fix
title: mem_0b1cca32
description: 'When adding DISTINCT support to Django aggregate classes (Avg, Sum, Min, Max), set `allow_distinct = True` on the class.'
resource: 'memeval://memory/mem_0b1cca32'
tags:
- django
- orm
- aggregates
timestamp: '2026-06-27T16:05:55.585820+00:00'
x_item_id: mem_0b1cca32
x_relevancy: 0.85
x_version: 1
x_session_id: cc56a3f7-9bea-443a-85db-ae1d516bd5c3
x_source: daydream
x_tokens: 46
---

When adding DISTINCT support to Django aggregate classes (Avg, Sum, Min, Max), set `allow_distinct = True` on the class. The base Aggregate class already handles DISTINCT SQL generation.
