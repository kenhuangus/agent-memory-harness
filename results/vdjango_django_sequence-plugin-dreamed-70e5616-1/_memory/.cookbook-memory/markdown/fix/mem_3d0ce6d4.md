---
type: Fix
title: mem_3d0ce6d4
description: When serializing JSON for admin display, json.dumps defaults to ensure_ascii=True which escapes non-ASCII characters.
resource: 'memeval://memory/mem_3d0ce6d4'
timestamp: '2026-06-28T02:15:33.687784+00:00'
x_item_id: mem_3d0ce6d4
x_relevancy: 0.95
x_version: 1
x_session_id: 7242f0e8-006e-4c41-bd25-147e6d40e7a1
x_source: daydream
x_tokens: 45
---

When serializing JSON for admin display, json.dumps defaults to ensure_ascii=True which escapes non-ASCII characters. Pass ensure_ascii=False in display_for_field to preserve Unicode.
