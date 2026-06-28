---
type: Fix
title: mem_dc42c267
description: When a QuerySet uses GROUP BY (e.g. after annotate()), default Meta.ordering is stripped from the SQL but QuerySet.ordered stays True — guard ordered with a check for group_by.
resource: 'memeval://memory/mem_dc42c267'
tags:
- django
- orm
- queryset
- ordering
timestamp: '2026-06-27T10:03:49.782521+00:00'
x_item_id: mem_dc42c267
x_relevancy: 0.95
x_version: 1
x_session_id: dab3365d-4343-40e4-a044-0687e7a01de4
x_source: daydream
x_tokens: 44
---

When a QuerySet uses GROUP BY (e.g. after annotate()), default Meta.ordering is stripped from the SQL but QuerySet.ordered stays True — guard ordered with a check for group_by.
