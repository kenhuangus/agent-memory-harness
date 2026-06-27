---
type: Memory
title: mem_a2b77bec
description: 'The fix for model_to_dict empty fields bug is replacing `if fields and f.name not in fields:` with `if fields is not None and f.name not in fields:` in django/forms/models.py line 86.'
resource: 'memeval://memory/mem_a2b77bec'
tags:
- fix
- django
timestamp: '2026-06-27T11:41:54.641898+00:00'
x_item_id: mem_a2b77bec
x_relevancy: 1.0
x_version: 1
x_session_id: 5bebe6d0-1c8a-4a4a-a575-8e58e48cc580
x_source: daydream
x_tokens: 45
---

The fix for model_to_dict empty fields bug is replacing `if fields and f.name not in fields:` with `if fields is not None and f.name not in fields:` in django/forms/models.py line 86.
