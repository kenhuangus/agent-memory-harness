---
type: Fix
title: mem_f9e3e506
description: 'When a SymPy simplification transform compares rv.exp using < or >, guard with `if not rv.exp.is_real: return rv` before the comparisons because complex exponents raise TypeError on ordering.'
resource: 'memeval://memory/mem_f9e3e506'
tags:
- sympy
timestamp: '2026-06-28T00:04:35.456691+00:00'
x_item_id: mem_f9e3e506
x_relevancy: 0.85
x_version: 1
x_session_id: 740ef6f6-c8ef-4cea-a2ee-f807ff0d268b
x_source: daydream
x_tokens: 47
---

When a SymPy simplification transform compares rv.exp using < or >, guard with `if not rv.exp.is_real: return rv` before the comparisons because complex exponents raise TypeError on ordering.
