---
type: Fix
title: mem_bf8690cb
description: When a SymPy constructor accepts both array and cycle forms, duplicate detection must apply only to array form; cycle form with overlapping elements composes via Cycle and should not be rejected.
resource: 'memeval://memory/mem_bf8690cb'
tags:
- sympy.combinatorics
- Permutation
timestamp: '2026-06-26T22:18:40.200889+00:00'
x_item_id: mem_bf8690cb
x_relevancy: 0.9
x_version: 1
x_session_id: 4d4684f3-9c28-42cd-a187-4ea7745af0b3
x_source: daydream
x_tokens: 48
---

When a SymPy constructor accepts both array and cycle forms, duplicate detection must apply only to array form; cycle form with overlapping elements composes via Cycle and should not be rejected.
