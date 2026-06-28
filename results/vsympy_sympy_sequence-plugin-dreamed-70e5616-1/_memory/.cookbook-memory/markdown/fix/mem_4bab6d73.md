---
type: Fix
title: mem_4bab6d73
description: 'When a custom numeric or vector type''s __add__ raises TypeError for scalar 0, add an early `if other == 0: return self` guard before the type check to fix sum() and zero-plus-vector operations.'
resource: 'memeval://memory/mem_4bab6d73'
tags:
- python
- arithmetic
- type-safety
timestamp: '2026-06-27T23:39:26.377248+00:00'
x_item_id: mem_4bab6d73
x_relevancy: 0.9
x_version: 1
x_session_id: a1e8bf6a-7454-4af7-9725-3f332f2e201b
x_source: daydream
x_tokens: 48
---

When a custom numeric or vector type's __add__ raises TypeError for scalar 0, add an early `if other == 0: return self` guard before the type check to fix sum() and zero-plus-vector operations.
