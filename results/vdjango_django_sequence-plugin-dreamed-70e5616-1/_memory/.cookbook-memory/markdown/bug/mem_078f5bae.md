---
type: Bug
title: mem_078f5bae
description: When a SQL compiler has both GROUP BY and _meta_ordering, it sets order_by = None — so the QuerySet-level property must mirror this same rule.
resource: 'memeval://memory/mem_078f5bae'
tags:
- django
- orm
- ordering
- annotate
timestamp: '2026-06-28T02:10:50.019063+00:00'
x_item_id: mem_078f5bae
x_relevancy: 1.0
x_version: 1
x_session_id: 670a4d4d-1e80-4c7e-b084-a8cd53b09ea1
x_source: daydream
x_tokens: 35
---

When a SQL compiler has both GROUP BY and _meta_ordering, it sets order_by = None — so the QuerySet-level property must mirror this same rule.
