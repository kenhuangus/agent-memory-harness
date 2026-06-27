---
type: Fix
title: mem_abc1da71
description: 'When a Django model instance is deleted via the fast-delete path (no dependencies), the instance''s primary key (pk) must be set to None after the SQL delete, matching the normal delete path.'
resource: 'memeval://memory/mem_abc1da71'
tags:
- django
timestamp: '2026-06-27T04:57:49.027585+00:00'
x_item_id: mem_abc1da71
x_relevancy: 0.95
x_version: 1
x_session_id: 95e2b612-9e56-4248-ae2a-01cc0f35e1b5
x_source: daydream
x_tokens: 47
---

When a Django model instance is deleted via the fast-delete path (no dependencies), the instance's primary key (pk) must be set to None after the SQL delete, matching the normal delete path.
