---
type: Fix
title: mem_cd46da17
description: 'When a simplification helper in fu.py (_TR56) compares an exponent with 0 or a bound, guard with an is_real check first because SymPy operations <, > on non-real (complex) values raise TypeError.'
resource: 'memeval://memory/mem_cd46da17'
tags:
- sympy
- trig
- fu
- comparison
- error-handling
timestamp: '2026-06-27T09:12:32.100266+00:00'
x_item_id: mem_cd46da17
x_relevancy: 0.95
x_version: 1
x_session_id: 6a1c7126-cd6f-449e-be40-7c34e5954029
x_source: daydream
x_tokens: 48
---

When a simplification helper in fu.py (_TR56) compares an exponent with 0 or a bound, guard with an is_real check first because SymPy operations <, > on non-real (complex) values raise TypeError.
