---
type: Workaround
title: mem_adc1ed08
description: 'When `_imp_` returns a non-Float SymPy expression, guard recursive evalf with a `free_symbols` check — only evalf if free_symbols is empty to avoid evaluating symbolic results.'
resource: 'memeval://memory/mem_adc1ed08'
tags:
- sympy
- evalf
- symbolic-results
timestamp: '2026-06-27T20:04:48.964924+00:00'
x_item_id: mem_adc1ed08
x_relevancy: 0.75
x_version: 1
x_session_id: 53732cde-da53-4441-b139-5580241a503f
x_source: daydream
x_tokens: 44
---

When `_imp_` returns a non-Float SymPy expression, guard recursive evalf with a `free_symbols` check — only evalf if free_symbols is empty to avoid evaluating symbolic results.
