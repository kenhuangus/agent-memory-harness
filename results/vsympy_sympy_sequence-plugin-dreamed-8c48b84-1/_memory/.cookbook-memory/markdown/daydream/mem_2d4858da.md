---
type: Memory
title: mem_2d4858da
description: 'Expressions like `(1 + I)**2` are not automatically simplified to `2*I` during Add construction; they remain as `Pow(1 + I, 2)` and may have `is_real=False`, `is_imaginary=None`.'
resource: 'memeval://memory/mem_2d4858da'
tags:
- internal detail
- Pow
- Add
timestamp: '2026-06-26T17:51:14.818851+00:00'
x_item_id: mem_2d4858da
x_relevancy: 0.7
x_version: 1
x_session_id: c2fa68f6-3403-4ca5-85e4-2258c40cf851
x_source: daydream
x_tokens: 44
---

Expressions like `(1 + I)**2` are not automatically simplified to `2*I` during Add construction; they remain as `Pow(1 + I, 2)` and may have `is_real=False`, `is_imaginary=None`.
