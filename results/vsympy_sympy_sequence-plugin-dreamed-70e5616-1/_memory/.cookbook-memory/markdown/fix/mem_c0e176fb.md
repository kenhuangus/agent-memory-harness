---
type: Fix
title: mem_c0e176fb
description: 'When implementing _entry(i, j) for a symbolic matrix class, do not use Python == or !=; return KroneckerDelta(i, j) instead to handle symbolic indices correctly.'
resource: 'memeval://memory/mem_c0e176fb'
tags:
- matrix-expressions
- symbolic-comparison
timestamp: '2026-06-28T00:56:46.908965+00:00'
x_item_id: mem_c0e176fb
x_relevancy: 0.95
x_version: 1
x_session_id: aea7408a-836f-41b9-a98d-3ceaefbf38e4
x_source: daydream
x_tokens: 40
---

When implementing _entry(i, j) for a symbolic matrix class, do not use Python == or !=; return KroneckerDelta(i, j) instead to handle symbolic indices correctly.
