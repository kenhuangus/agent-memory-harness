---
type: daydream
title: mem_1f3f2f71
description: 'The fix for `Function._eval_evalf` not calling `_imp_` recursively is in `sympy/core/function.py`: when `_imp_` is defined and has args, ev…'
resource: 'memeval://memory/mem_1f3f2f71'
tags:
- fix-pattern
- evalf
- implemented_function
timestamp: '2026-06-25T04:46:08.008162+00:00'
x_item_id: mem_1f3f2f71
x_relevancy: 1.0
x_version: 1
x_session_id: 7368e31c-03d7-4d66-bcfe-376ebff505d7
x_source: daydream
x_tokens: 50
x_metadata_json: '{"extracted_from": "7368e31c-03d7-4d66-bcfe-376ebff505d7"}'
---

The fix for `Function._eval_evalf` not calling `_imp_` recursively is in `sympy/core/function.py`: when `_imp_` is defined and has args, evaluate `_imp_(*args)` and then call `.evalf()` on the result.
