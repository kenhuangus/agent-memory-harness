---
type: Fix
title: mem_57f5f7ac
description: 'When implementing `_eval_is_zero` for `Add`, if no term is known to be non-zero, return `None` (undecided) instead of `False`, as the sum may still be zero (e.g., imaginary terms cancel).'
resource: 'memeval://memory/mem_57f5f7ac'
tags:
- sympy
- core
- is_zero
timestamp: '2026-06-26T22:35:00.247640+00:00'
x_item_id: mem_57f5f7ac
x_relevancy: 0.95
x_version: 1
x_session_id: 662709e2-3735-46f2-9a72-c004a1a55c85
x_source: daydream
x_tokens: 46
---

When implementing `_eval_is_zero` for `Add`, if no term is known to be non-zero, return `None` (undecided) instead of `False`, as the sum may still be zero (e.g., imaginary terms cancel).
