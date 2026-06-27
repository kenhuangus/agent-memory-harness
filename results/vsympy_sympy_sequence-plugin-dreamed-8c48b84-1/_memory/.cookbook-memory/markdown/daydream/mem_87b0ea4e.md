---
type: Memory
title: mem_87b0ea4e
description: 'The `__lt__` method in `sympy/core/expr.py` raises `TypeError("Invalid NaN comparison")` at line 335-336 when `me is S.NaN`, which is the exact exception caught by the fix in exprtools.py.'
resource: 'memeval://memory/mem_87b0ea4e'
tags:
- codebase-fact
- architecture
timestamp: '2026-06-26T20:01:45.253899+00:00'
x_item_id: mem_87b0ea4e
x_relevancy: 0.8
x_version: 1
x_session_id: c590b2ee-0551-49ad-95dd-633e26b739e1
x_source: daydream
x_tokens: 47
---

The `__lt__` method in `sympy/core/expr.py` raises `TypeError("Invalid NaN comparison")` at line 335-336 when `me is S.NaN`, which is the exact exception caught by the fix in exprtools.py.
