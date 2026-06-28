---
type: Fix
title: mem_1f23b6d4
description: 'When checking for imaginary components in SymPy coordinates under evaluate(False), use `im(a).is_zero is False` instead of `bool(im(a))` to avoid false positives where `im` remains unevaluated.'
resource: 'memeval://memory/mem_1f23b6d4'
timestamp: '2026-06-28T00:36:40.868265+00:00'
x_item_id: mem_1f23b6d4
x_relevancy: 0.95
x_version: 1
x_session_id: 7b559f92-71cc-4480-a2a1-019fcdd99b0f
x_source: daydream
x_tokens: 48
---

When checking for imaginary components in SymPy coordinates under evaluate(False), use `im(a).is_zero is False` instead of `bool(im(a))` to avoid false positives where `im` remains unevaluated.
