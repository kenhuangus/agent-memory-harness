---
type: Bug
title: mem_0e3d2c08
description: 'When _sqrt_match checks all(x**2).is_Rational before calling split_surds, also require is_positive to avoid IndexError from empty surd list.'
resource: 'memeval://memory/mem_0e3d2c08'
tags:
- sqrtdenest
- IndexError
- split_surds
timestamp: '2026-06-27T06:43:19.341531+00:00'
x_item_id: mem_0e3d2c08
x_relevancy: 0.95
x_version: 1
x_session_id: b6e536b2-11a3-4e00-9776-af357cdcbe55
x_source: daydream
x_tokens: 35
---

When _sqrt_match checks all(x**2).is_Rational before calling split_surds, also require is_positive to avoid IndexError from empty surd list.
