---
type: Fix
title: mem_8cd55e28
description: When computing the product of elements in an iterable that may be empty, use reduce with initializer=1 so the empty product yields 1 instead of 0.
resource: 'memeval://memory/mem_8cd55e28'
tags:
- python
- sympy
- tensor
timestamp: '2026-06-27T15:42:26.135019+00:00'
x_item_id: mem_8cd55e28
x_relevancy: 0.95
x_version: 1
x_session_id: b9512300-37f5-4adc-a5c1-f7e6e2c5f18a
x_source: daydream
x_tokens: 36
---

When computing the product of elements in an iterable that may be empty, use reduce with initializer=1 so the empty product yields 1 instead of 0.
