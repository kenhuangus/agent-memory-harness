---
type: Memory
title: mem_fe1ef85e
description: The has_changed() method in django/forms/fields.py also uses json.dumps for comparison and needs ensure_ascii=False for consistency with Unicode values.
resource: 'memeval://memory/mem_fe1ef85e'
tags:
- bug-fix
- django
- jsonfield
timestamp: '2026-06-26T18:05:42.871899+00:00'
x_item_id: mem_fe1ef85e
x_relevancy: 0.7
x_version: 1
x_session_id: e0a2fa13-536b-4a29-9cd2-414b9a430c3b
x_source: daydream
x_tokens: 38
---

The has_changed() method in django/forms/fields.py also uses json.dumps for comparison and needs ensure_ascii=False for consistency with Unicode values.
