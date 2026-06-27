---
type: Fix
title: mem_57f91aa8
description: 'When writing SymPy simplification rules that compare exponent values with `<` or `>`, guard against non-real exponents by checking `rv.exp.is_real is False` before the comparison to avoid `TypeError`.'
resource: 'memeval://memory/mem_57f91aa8'
tags:
- sympy
- trigsimp
- fu
- fix
timestamp: '2026-06-27T15:59:42.283577+00:00'
x_item_id: mem_57f91aa8
x_relevancy: 0.9
x_version: 1
x_session_id: 5bbb89ad-04ff-43b0-95f9-c22da8e48992
x_source: daydream
x_tokens: 50
---

When writing SymPy simplification rules that compare exponent values with `<` or `>`, guard against non-real exponents by checking `rv.exp.is_real is False` before the comparison to avoid `TypeError`.
