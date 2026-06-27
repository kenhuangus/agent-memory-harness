---
type: Fix
title: mem_f9ca0602
description: 'When printing compound objects (Subs, Derivative, Integral) in SymPy''s LaTeX printer, use self.parenthesize(expr, PRECEDENCE[''Mul''], strict=True) to wrap inner expressions with proper parentheses.'
resource: 'memeval://memory/mem_f9ca0602'
tags:
- printing
- latex
- subs
- parenthesization
timestamp: '2026-06-27T05:20:28.254265+00:00'
x_item_id: mem_f9ca0602
x_relevancy: 0.9
x_version: 1
x_session_id: bb684e35-1d19-4fd5-98f9-d05614a874c3
x_source: daydream
x_tokens: 49
---

When printing compound objects (Subs, Derivative, Integral) in SymPy's LaTeX printer, use self.parenthesize(expr, PRECEDENCE['Mul'], strict=True) to wrap inner expressions with proper parentheses.
