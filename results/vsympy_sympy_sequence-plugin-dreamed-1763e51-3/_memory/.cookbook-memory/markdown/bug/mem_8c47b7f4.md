---
type: Bug
title: mem_8c47b7f4
description: 'When nsolve uses lambdify with modules=''mpmath'', any SymPy Rational in the expression must be printed as mpmath.mpf(p)/mpmath.mpf(q); otherwise nsolve''s high-precision mode is silently degraded.'
resource: 'memeval://memory/mem_8c47b7f4'
timestamp: '2026-06-27T17:25:48.499586+00:00'
x_item_id: mem_8c47b7f4
x_relevancy: 0.9
x_version: 1
x_session_id: be61e4eb-315e-40b3-9ccd-230d2fa3241d
x_source: daydream
x_tokens: 48
---

When nsolve uses lambdify with modules='mpmath', any SymPy Rational in the expression must be printed as mpmath.mpf(p)/mpmath.mpf(q); otherwise nsolve's high-precision mode is silently degraded.
