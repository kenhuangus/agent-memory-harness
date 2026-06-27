---
type: Memory
title: mem_b9671bfd
description: 'The `_eval_is_zero` method in `Add` can incorrectly return `False` for expressions with canceling imaginary parts, e.g., `-2*I + (1 + I)**2 == 0` but `is_zero` returned `False`.'
resource: 'memeval://memory/mem_b9671bfd'
tags:
- bug
- is_zero
- Add
- assumptions
timestamp: '2026-06-26T17:51:14.818851+00:00'
x_item_id: mem_b9671bfd
x_relevancy: 0.95
x_version: 1
x_session_id: c2fa68f6-3403-4ca5-85e4-2258c40cf851
x_source: daydream
x_tokens: 44
---

The `_eval_is_zero` method in `Add` can incorrectly return `False` for expressions with canceling imaginary parts, e.g., `-2*I + (1 + I)**2 == 0` but `is_zero` returned `False`.
