---
type: Fix
title: mem_c1551c8f
description: When ForeignKey.validate uses _default_manager for existence checks, replace it with _base_manager to avoid false negatives from custom manager filters.
resource: 'memeval://memory/mem_c1551c8f'
tags:
- django
- orm
- validation
timestamp: '2026-06-27T16:55:21.047594+00:00'
x_item_id: mem_c1551c8f
x_relevancy: 0.9
x_version: 1
x_session_id: 315fdaef-f54a-4e31-a52c-d75f0873d843
x_source: daydream
x_tokens: 38
---

When ForeignKey.validate uses _default_manager for existence checks, replace it with _base_manager to avoid false negatives from custom manager filters.
