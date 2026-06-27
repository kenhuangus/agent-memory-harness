---
type: Memory
title: mem_8ca1ba52
description: 'A `_print_Rational` method was added to MpmathPrinter that wraps Rational in `mpmath.mpf(p, q)`: `return ''{func}({p}, {q})''.format(func=self._module_format(''mpmath.mpf''), p=int(e.p), q=int(e.q))`.'
resource: 'memeval://memory/mem_8ca1ba52'
tags:
- sympy
- fix
- code
timestamp: '2026-06-26T20:23:24.718016+00:00'
x_item_id: mem_8ca1ba52
x_relevancy: 1.0
x_version: 1
x_session_id: 9f9dd53d-f07e-4e42-bcdd-f189c62d0468
x_source: daydream
x_tokens: 49
---

A `_print_Rational` method was added to MpmathPrinter that wraps Rational in `mpmath.mpf(p, q)`: `return '{func}({p}, {q})'.format(func=self._module_format('mpmath.mpf'), p=int(e.p), q=int(e.q))`.
