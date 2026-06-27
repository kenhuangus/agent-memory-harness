---
type: Fix
title: mem_3dd997d1
description: When a symbolic matrix class overrides _entry(i, j) using Python == (i == j), it silently returns 0 for symbolic indices.
resource: 'memeval://memory/mem_3dd997d1'
tags:
- matrix_expressions
- symbolic_comparison
timestamp: '2026-06-26T23:45:36.440735+00:00'
x_item_id: mem_3dd997d1
x_relevancy: 0.95
x_version: 1
x_session_id: bbf8c0c7-c734-443a-8485-a910a2b8b0b9
x_source: daydream
x_tokens: 48
---

When a symbolic matrix class overrides _entry(i, j) using Python == (i == j), it silently returns 0 for symbolic indices. Use Eq(i, j) and fall back to KroneckerDelta(i, j) when Eq is unresolved.
