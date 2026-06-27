---
type: Memory
title: mem_2ee94f2e
description: 'The bug: `evalf()` on compositions of `implemented_function` like `f(g(2)).evalf()` returned the unevaluated expression `f(g(2))` instead of `16.0`.'
resource: 'memeval://memory/mem_2ee94f2e'
tags:
- bug
- evalf
- composition
timestamp: '2026-06-26T17:16:09.505625+00:00'
x_item_id: mem_2ee94f2e
x_relevancy: 0.9
x_version: 1
x_session_id: 8835b592-c246-41be-9177-af5410d71813
x_source: daydream
x_tokens: 37
---

The bug: `evalf()` on compositions of `implemented_function` like `f(g(2)).evalf()` returned the unevaluated expression `f(g(2))` instead of `16.0`.
