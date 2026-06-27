---
type: Fix
title: mem_82fe78cc
description: 'When adding a fast-delete optimization path in a Django model''s delete logic, the fast path must also set instance.pk to None after deletion, to match the behavior of the normal delete path.'
resource: 'memeval://memory/mem_82fe78cc'
tags:
- Django
- flush
- delete
timestamp: '2026-06-27T15:31:38.709195+00:00'
x_item_id: mem_82fe78cc
x_relevancy: 0.9
x_version: 1
x_session_id: c4561a28-655f-4e37-85c7-3240969adad5
x_source: daydream
x_tokens: 47
---

When adding a fast-delete optimization path in a Django model's delete logic, the fast path must also set instance.pk to None after deletion, to match the behavior of the normal delete path.
