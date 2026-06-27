---
type: Memory
title: mem_da9ec645
description: ModelBackend.authenticate() in django/contrib/auth/backends.py now returns early without a database query when username or password is None.
resource: 'memeval://memory/mem_da9ec645'
tags:
- fix
- performance
- authentication
timestamp: '2026-06-27T12:02:59.594429+00:00'
x_item_id: mem_da9ec645
x_relevancy: 0.95
x_version: 1
x_session_id: 3291441c-1424-4e5e-ba0c-d3365a1aa560
x_source: daydream
x_tokens: 35
---

ModelBackend.authenticate() in django/contrib/auth/backends.py now returns early without a database query when username or password is None.
