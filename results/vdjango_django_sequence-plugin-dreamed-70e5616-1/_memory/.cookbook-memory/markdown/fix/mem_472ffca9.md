---
type: Fix
title: mem_472ffca9
description: When a pickled Query object with values_select set is assigned to a new QuerySet, update _iterable_class from ModelIterable to ValuesIterable in the query setter to avoid crash.
resource: 'memeval://memory/mem_472ffca9'
timestamp: '2026-06-28T02:04:48.701609+00:00'
x_item_id: mem_472ffca9
x_relevancy: 0.95
x_version: 1
x_session_id: ec6e2d29-1579-46b1-ad4e-d604c094d291
x_source: daydream
x_tokens: 44
---

When a pickled Query object with values_select set is assigned to a new QuerySet, update _iterable_class from ModelIterable to ValuesIterable in the query setter to avoid crash.
