---
type: Fix
title: mem_f57c1ba8
description: 'When implementing a generator that yields mutable collections (dict, list, set), yield a copy (`.copy()` for dicts, `[:]` for lists) instead of the mutated original to prevent aliasing bugs.'
resource: 'memeval://memory/mem_f57c1ba8'
tags:
- python
- generators
timestamp: '2026-06-28T02:56:38.132401+00:00'
x_item_id: mem_f57c1ba8
x_relevancy: 1.0
x_version: 1
x_session_id: 256f4abc-246e-48ca-a4ba-16f646c494fc
x_source: daydream
x_tokens: 47
---

When implementing a generator that yields mutable collections (dict, list, set), yield a copy (`.copy()` for dicts, `[:]` for lists) instead of the mutated original to prevent aliasing bugs.
