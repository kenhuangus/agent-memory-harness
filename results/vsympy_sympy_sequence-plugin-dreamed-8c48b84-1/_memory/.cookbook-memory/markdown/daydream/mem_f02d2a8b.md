---
type: Memory
title: mem_f02d2a8b
description: 'The `symbols()` function handles string input directly using `cls()`, but for non-string sequences (tuple/list/set) it recurses per element and preserves the container type via `type(names)(result)`.'
resource: 'memeval://memory/mem_f02d2a8b'
tags:
- symbols
- design
- recursion
timestamp: '2026-06-26T18:55:32.472315+00:00'
x_item_id: mem_f02d2a8b
x_relevancy: 0.85
x_version: 1
x_session_id: f9eaa8b6-21a0-4c88-89e8-0bcb66a81a4d
x_source: daydream
x_tokens: 49
---

The `symbols()` function handles string input directly using `cls()`, but for non-string sequences (tuple/list/set) it recurses per element and preserves the container type via `type(names)(result)`.
