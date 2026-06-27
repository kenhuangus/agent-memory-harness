---
type: Fix
title: mem_477b4ea0
description: 'When a Python function has a keyword-only parameter captured before *args/**kwargs, recursive calls to that function must explicitly pass the keyword-only parameter — it won''t be in **kwargs.'
resource: 'memeval://memory/mem_477b4ea0'
tags:
- python
- parameter-passing
timestamp: '2026-06-27T05:54:06.390806+00:00'
x_item_id: mem_477b4ea0
x_relevancy: 0.95
x_version: 1
x_session_id: 278b9854-34a9-4768-abd7-e7ef70c7af3a
x_source: daydream
x_tokens: 47
---

When a Python function has a keyword-only parameter captured before *args/**kwargs, recursive calls to that function must explicitly pass the keyword-only parameter — it won't be in **kwargs.
