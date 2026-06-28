---
type: Bug
title: mem_1c00f7f0
description: 'When a generator/iterator yields a mutable object (dict, list, set) and `list(gen())` produces identical items, the root cause is the generator reusing a single mutable instance modified in place.'
resource: 'memeval://memory/mem_1c00f7f0'
tags:
- python
- iterators
timestamp: '2026-06-28T02:56:38.132401+00:00'
x_item_id: mem_1c00f7f0
x_relevancy: 1.0
x_version: 1
x_session_id: 256f4abc-246e-48ca-a4ba-16f646c494fc
x_source: daydream
x_tokens: 49
---

When a generator/iterator yields a mutable object (dict, list, set) and `list(gen())` produces identical items, the root cause is the generator reusing a single mutable instance modified in place.
