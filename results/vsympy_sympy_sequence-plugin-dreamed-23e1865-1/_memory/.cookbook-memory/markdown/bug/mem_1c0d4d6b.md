---
type: Bug
title: mem_1c0d4d6b
description: 'When evaluating products of additive expressions symbolically, ∏(a + b) ≠ ∏(a) + ∏(b); never split an Add term via `as_coeff_Add()` and sum the individual products — return `None` instead.'
resource: 'memeval://memory/mem_1c0d4d6b'
tags:
- sympy
- product
- add
- bug
timestamp: '2026-06-27T17:53:31.130834+00:00'
x_item_id: mem_1c0d4d6b
x_relevancy: 0.95
x_version: 1
x_session_id: 37f28081-bd3b-4b3e-8153-01cd60e3f993
x_source: daydream
x_tokens: 47
---

When evaluating products of additive expressions symbolically, ∏(a + b) ≠ ∏(a) + ∏(b); never split an Add term via `as_coeff_Add()` and sum the individual products — return `None` instead.
