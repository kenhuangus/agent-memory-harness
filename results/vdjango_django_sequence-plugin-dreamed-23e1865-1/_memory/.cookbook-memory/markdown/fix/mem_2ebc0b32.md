---
type: Fix
title: mem_2ebc0b32
description: 'When `Collector.delete()` has a fast-delete path for a single model instance with no dependencies, the PK must be cleared on the instance before returning, just like the normal path does.'
resource: 'memeval://memory/mem_2ebc0b32'
tags:
- django
- delete
- pk
timestamp: '2026-06-27T15:50:45.377781+00:00'
x_item_id: mem_2ebc0b32
x_relevancy: 0.95
x_version: 1
x_session_id: 73520b85-af65-41d3-86ad-6656b59760f2
x_source: daydream
x_tokens: 46
---

When `Collector.delete()` has a fast-delete path for a single model instance with no dependencies, the PK must be cleared on the instance before returning, just like the normal path does.
