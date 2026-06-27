---
type: Memory
title: mem_5a7c78a4
description: 'The fix was a one-line change in Permutation.__new__: removed the ValueError raised when `has_dups(temp) and is_cycle`; the error now only fires for array form (`not is_cycle`).'
resource: 'memeval://memory/mem_5a7c78a4'
tags:
- sympy
- code-change
- permutation
timestamp: '2026-06-26T05:08:21.204317+00:00'
x_item_id: mem_5a7c78a4
x_relevancy: 0.85
x_version: 1
x_session_id: 26453c5e-9ab4-4816-b0b3-0ef217d78324
x_source: daydream
x_tokens: 44
---

The fix was a one-line change in Permutation.__new__: removed the ValueError raised when `has_dups(temp) and is_cycle`; the error now only fires for array form (`not is_cycle`).
