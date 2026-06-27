---
type: Fix
title: mem_8ecfbdd2
description: 'When printing a wrapped expression (Subs, Derivative) in LaTeX, use `self.parenthesize(expr, PRECEDENCE[''Mul''], strict=True)` instead of `self._print(expr)` for correct parentheses.'
resource: 'memeval://memory/mem_8ecfbdd2'
tags:
- printing
- latex
- parenthisis
timestamp: '2026-06-27T16:06:04.156197+00:00'
x_item_id: mem_8ecfbdd2
x_relevancy: 0.9
x_version: 1
x_session_id: 6fe8e118-83f5-4f29-ae02-625a9db34721
x_source: daydream
x_tokens: 45
---

When printing a wrapped expression (Subs, Derivative) in LaTeX, use `self.parenthesize(expr, PRECEDENCE['Mul'], strict=True)` instead of `self._print(expr)` for correct parentheses.
