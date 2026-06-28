---
type: Fix
title: mem_b9fe5ad0
description: 'When `functools.reduce` computes product of an empty iterable (e.g., shape tuple for rank-0 array), pass initializer `1` instead of guarding with `if shape else 0`.'
resource: 'memeval://memory/mem_b9fe5ad0'
tags:
- sympy
- ndim_array
- reduce-pitfall
timestamp: '2026-06-27T23:43:02.911902+00:00'
x_item_id: mem_b9fe5ad0
x_relevancy: 0.95
x_version: 1
x_session_id: 85148740-57d5-41ed-b49a-0fe0608bcd5b
x_source: daydream
x_tokens: 41
---

When `functools.reduce` computes product of an empty iterable (e.g., shape tuple for rank-0 array), pass initializer `1` instead of guarding with `if shape else 0`.
