---
type: Bug
title: mem_8dad5485
description: 'In `tensor_product_simp_Mul`, a single non-commutative factor (`n_nc == 1`) can be a `Pow` of `TensorProduct`; do not return early without checking for that case, or powers will remain unsimplified.'
resource: 'memeval://memory/mem_8dad5485'
tags:
- sympy
- quantum
- tensor_product_simp
timestamp: '2026-06-27T21:38:41.758242+00:00'
x_item_id: mem_8dad5485
x_relevancy: 0.8
x_version: 1
x_session_id: 2332e94d-fed6-4cfe-8708-ee94eb888d59
x_source: daydream
x_tokens: 49
---

In `tensor_product_simp_Mul`, a single non-commutative factor (`n_nc == 1`) can be a `Pow` of `TensorProduct`; do not return early without checking for that case, or powers will remain unsimplified.
