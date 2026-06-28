---
type: Fix
title: mem_59a94cd2
description: 'When implementing `_eval_evalf` in a `Function` that uses `_imp_`, recursively evalf arguments first to resolve nested symbolic expressions, else composition of implemented functions fails.'
resource: 'memeval://memory/mem_59a94cd2'
tags:
- sympy
- evalf
timestamp: '2026-06-27T19:58:57.263142+00:00'
x_item_id: mem_59a94cd2
x_relevancy: 0.9
x_version: 1
x_session_id: 85c81e3b-2ec1-46d1-9e19-c181277184af
x_source: daydream
x_tokens: 47
---

When implementing `_eval_evalf` in a `Function` that uses `_imp_`, recursively evalf arguments first to resolve nested symbolic expressions, else composition of implemented functions fails.
