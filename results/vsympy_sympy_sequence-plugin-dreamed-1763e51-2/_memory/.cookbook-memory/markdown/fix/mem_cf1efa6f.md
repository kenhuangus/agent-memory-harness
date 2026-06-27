---
type: Fix
title: mem_cf1efa6f
description: 'When implementing tensor_product_simp, a Pow whose base is a TensorProduct must be simplified by applying the exponent to each factor: `TensorProduct(a,b)**n -> TensorProduct(a**n, b**n)`.'
resource: 'memeval://memory/mem_cf1efa6f'
tags:
- quantum
- tensorproduct
timestamp: '2026-06-27T06:20:45.766935+00:00'
x_item_id: mem_cf1efa6f
x_relevancy: 0.8
x_version: 1
x_session_id: 3a7d2974-979e-4f42-acf7-5d99d21c9a8e
x_source: daydream
x_tokens: 47
---

When implementing tensor_product_simp, a Pow whose base is a TensorProduct must be simplified by applying the exponent to each factor: `TensorProduct(a,b)**n -> TensorProduct(a**n, b**n)`.
