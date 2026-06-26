---
type: Memory
title: mem_af7a6b1d
description: 'Vector.__add__ does not accept scalar 0; the pattern check (if other == 0: other = Vector(0)) must be added before _check_vector(other).'
resource: 'memeval://memory/mem_af7a6b1d'
tags:
- sympy
- vector-add
- python-bug
timestamp: '2026-06-26T05:14:28.501920+00:00'
x_item_id: mem_af7a6b1d
x_relevancy: 0.95
x_version: 1
x_session_id: a5201c24-05f9-4a08-bc0f-dc8a29a5723a
x_source: daydream
x_tokens: 34
---

Vector.__add__ does not accept scalar 0; the pattern check (if other == 0: other = Vector(0)) must be added before _check_vector(other).
