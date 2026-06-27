---
type: Memory
title: mem_dc62428f
description: 'Matrix rank computation in `sympy/matrices/matrices.py` uses `v[rank:, 0].is_zero` to detect inconsistency in linear systems, so incorrect `is_zero` returning `False` affects matrix rank.'
resource: 'memeval://memory/mem_dc62428f'
tags:
- matrices
- rank
- linear algebra
timestamp: '2026-06-26T17:51:14.818851+00:00'
x_item_id: mem_dc62428f
x_relevancy: 0.75
x_version: 1
x_session_id: c2fa68f6-3403-4ca5-85e4-2258c40cf851
x_source: daydream
x_tokens: 46
---

Matrix rank computation in `sympy/matrices/matrices.py` uses `v[rank:, 0].is_zero` to detect inconsistency in linear systems, so incorrect `is_zero` returning `False` affects matrix rank.
