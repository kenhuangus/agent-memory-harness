---
type: Bug
title: mem_c4ec952b
description: When a DMP has an unstripped leading zero list element (e.g.
resource: 'memeval://memory/mem_c4ec952b'
tags:
- bug-pattern
- polynomials
timestamp: '2026-06-27T12:55:34.167757+00:00'
x_item_id: mem_c4ec952b
x_relevancy: 0.9
x_version: 1
x_session_id: ca740a4e-9f78-4f86-ba5a-603ab5fd2d33
x_source: daydream
x_tokens: 43
---

When a DMP has an unstripped leading zero list element (e.g. DMP([EX(0)]) instead of DMP([])), is_zero returns False while as_expr() returns 0, causing inconsistent behavior.
