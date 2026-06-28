---
type: Fix
title: mem_cc4d685f
description: 'When `Function._eval_evalf` calls `_imp_` with unevaluated symbolic args from composition, evaluate each arg via `.evalf(prec)` first and recursively evalf the result if it remains a SymPy expression.'
resource: 'memeval://memory/mem_cc4d685f'
tags:
- sympy
- evalf
- implemented-function
timestamp: '2026-06-27T20:04:48.964924+00:00'
x_item_id: mem_cc4d685f
x_relevancy: 0.85
x_version: 1
x_session_id: 53732cde-da53-4441-b139-5580241a503f
x_source: daydream
x_tokens: 50
---

When `Function._eval_evalf` calls `_imp_` with unevaluated symbolic args from composition, evaluate each arg via `.evalf(prec)` first and recursively evalf the result if it remains a SymPy expression.
