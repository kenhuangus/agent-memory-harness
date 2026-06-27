---
type: Fix
title: mem_76eedc1a
description: 'When `Permutation.__new__` receives cycle form (list of lists), duplicate detection across cycles should be skipped — cycles compose left-to-right and overlap is allowed.'
resource: 'memeval://memory/mem_76eedc1a'
tags:
- sympy
- combinatorics
- permutations
timestamp: '2026-06-27T04:45:46.394416+00:00'
x_item_id: mem_76eedc1a
x_relevancy: 0.95
x_version: 1
x_session_id: 3686ddff-7401-4bfe-bff5-c4bf1cfbe77a
x_source: daydream
x_tokens: 42
---

When `Permutation.__new__` receives cycle form (list of lists), duplicate detection across cycles should be skipped — cycles compose left-to-right and overlap is allowed.
