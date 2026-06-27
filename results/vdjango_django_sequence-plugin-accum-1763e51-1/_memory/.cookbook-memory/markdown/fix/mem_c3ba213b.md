---
type: Fix
title: mem_c3ba213b
description: 'When using `cursor.fetchone()` and subscripting the result in a cache culling or similar cleanup operation, guard against `None` — concurrent deletions can cause it to return no row.'
resource: 'memeval://memory/mem_c3ba213b'
tags:
- django
- database cache
- culling
- concurrency
timestamp: '2026-06-27T06:00:32.791229+00:00'
x_item_id: mem_c3ba213b
x_relevancy: 0.9
x_version: 1
x_session_id: 46f2f032-695c-4bd3-985f-d2d702299d4c
x_source: daydream
x_tokens: 45
---

When using `cursor.fetchone()` and subscripting the result in a cache culling or similar cleanup operation, guard against `None` — concurrent deletions can cause it to return no row.
