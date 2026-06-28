---
type: Bug
title: mem_fc7ab126
description: When a query has values_select populated, the SELECT clause no longer carries full model fields, making ModelIterable unusable.
resource: 'memeval://memory/mem_fc7ab126'
timestamp: '2026-06-28T02:04:48.701609+00:00'
x_item_id: mem_fc7ab126
x_relevancy: 0.9
x_version: 1
x_session_id: ec6e2d29-1579-46b1-ad4e-d604c094d291
x_source: daydream
x_tokens: 46
---

When a query has values_select populated, the SELECT clause no longer carries full model fields, making ModelIterable unusable. ValuesIterable or ValuesListIterable must be used instead.
