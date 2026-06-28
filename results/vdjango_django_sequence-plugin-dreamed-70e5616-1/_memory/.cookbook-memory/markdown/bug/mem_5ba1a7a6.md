---
type: Bug
title: mem_5ba1a7a6
description: When a Django ORM compiler method iterates Meta.ordering and passes items to get_order_dir, guard against expression objects (OrderBy) with isinstance check to avoid crash.
resource: 'memeval://memory/mem_5ba1a7a6'
timestamp: '2026-06-27T20:30:27.694121+00:00'
x_item_id: mem_5ba1a7a6
x_relevancy: 0.9
x_version: 1
x_session_id: 8771bea1-94ff-40d4-b617-286223634a2e
x_source: daydream
x_tokens: 43
---

When a Django ORM compiler method iterates Meta.ordering and passes items to get_order_dir, guard against expression objects (OrderBy) with isinstance check to avoid crash.
