---
type: Memory
title: mem_574b4ebb
description: 'The fix for model_to_dict() is to change the condition from `if fields and f.name not in fields:` to `if fields is not None and f.name not in fields:` on line 86 of django/forms/models.py.'
resource: 'memeval://memory/mem_574b4ebb'
tags:
- fix
- code-change
timestamp: '2026-06-27T11:43:22.515316+00:00'
x_item_id: mem_574b4ebb
x_relevancy: 1.0
x_version: 1
x_session_id: 2c5179ee-6f2f-499f-9d95-8d4b6eb66162
x_source: daydream
x_tokens: 47
---

The fix for model_to_dict() is to change the condition from `if fields and f.name not in fields:` to `if fields is not None and f.name not in fields:` on line 86 of django/forms/models.py.
