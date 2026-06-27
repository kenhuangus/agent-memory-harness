---
type: Bug
title: mem_fd185f4a
description: When a Django QuerySet has default ordering and a GROUP BY clause (from aggregate annotations), the SQL compiler removes the ORDER BY clause, making the queryset effectively unordered.
resource: 'memeval://memory/mem_fd185f4a'
tags:
- django
- queryset
- ordering
- group-by
timestamp: '2026-06-27T17:45:09.509820+00:00'
x_item_id: mem_fd185f4a
x_relevancy: 0.9
x_version: 1
x_session_id: f97b7798-fda0-4833-a34a-1aedb60ba88f
x_source: daydream
x_tokens: 46
---

When a Django QuerySet has default ordering and a GROUP BY clause (from aggregate annotations), the SQL compiler removes the ORDER BY clause, making the queryset effectively unordered.
