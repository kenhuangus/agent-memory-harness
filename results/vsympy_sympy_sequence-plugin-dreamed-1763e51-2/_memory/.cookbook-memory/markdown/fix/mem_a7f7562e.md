---
type: Fix
title: mem_a7f7562e
description: When factoring multivariate polys over algebraic extensions via dmp_ext_factor, pass the full polynomial (F) to dmp_sqf_norm, not the square-free part (f), or some factors may drop.
resource: 'memeval://memory/mem_a7f7562e'
tags:
- sympy
- polynomials
- factorization
- algebraic extension
- bug fix
timestamp: '2026-06-27T00:33:57.253733+00:00'
x_item_id: mem_a7f7562e
x_relevancy: 0.9
x_version: 1
x_session_id: 43bb1eae-c94f-4cdf-8e05-22519aad5c54
x_source: daydream
x_tokens: 45
---

When factoring multivariate polys over algebraic extensions via dmp_ext_factor, pass the full polynomial (F) to dmp_sqf_norm, not the square-free part (f), or some factors may drop.
