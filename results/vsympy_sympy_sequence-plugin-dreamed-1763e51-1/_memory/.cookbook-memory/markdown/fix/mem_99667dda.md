---
type: Fix
title: mem_99667dda
description: 'When implementing `_entry` for SymPy matrix expressions, use `Eq(i, j)` (not `==`) and fall back to `KroneckerDelta(i, j)` for unresolved symbolic indices.'
resource: 'memeval://memory/mem_99667dda'
timestamp: '2026-06-27T09:55:33.498397+00:00'
x_item_id: mem_99667dda
x_relevancy: 0.95
x_version: 1
x_session_id: 57fedaa1-534e-4bfb-b639-a7dcbaef4368
x_source: daydream
x_tokens: 38
---

When implementing `_entry` for SymPy matrix expressions, use `Eq(i, j)` (not `==`) and fall back to `KroneckerDelta(i, j)` for unresolved symbolic indices.
