---
type: Memory
title: mem_b6b676d5
description: 'The fix for clearing PKs on model deletion when no dependencies exist is to add `setattr(instance, model._meta.pk.attname, None)` in the fast-delete early-return path before the return statement.'
resource: 'memeval://memory/mem_b6b676d5'
tags:
- Django
- ORM
- deletion
- bugfix
timestamp: '2026-06-27T04:51:49.637794+00:00'
x_item_id: mem_b6b676d5
x_relevancy: 0.95
x_version: 1
x_session_id: ac4d6aa9-3c83-451b-9ce9-032f661f104d
x_source: daydream
x_tokens: 48
---

The fix for clearing PKs on model deletion when no dependencies exist is to add `setattr(instance, model._meta.pk.attname, None)` in the fast-delete early-return path before the return statement.
