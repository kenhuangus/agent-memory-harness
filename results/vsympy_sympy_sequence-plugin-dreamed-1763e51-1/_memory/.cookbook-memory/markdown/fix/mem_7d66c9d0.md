---
type: Fix
title: mem_7d66c9d0
description: 'In `_symbolic_factor_list`, after splitting Mul args into coeff and factor list, group factors with the same exponent and replace them by a single `(Mul(*bases), k)` when `method == ''sqf''`.'
resource: 'memeval://memory/mem_7d66c9d0'
tags:
- polytools
- sqf
timestamp: '2026-06-27T10:50:11.520508+00:00'
x_item_id: mem_7d66c9d0
x_relevancy: 0.9
x_version: 1
x_session_id: 08fa870e-239a-4ec5-ad47-8c45891ab8b3
x_source: daydream
x_tokens: 47
---

In `_symbolic_factor_list`, after splitting Mul args into coeff and factor list, group factors with the same exponent and replace them by a single `(Mul(*bases), k)` when `method == 'sqf'`.
