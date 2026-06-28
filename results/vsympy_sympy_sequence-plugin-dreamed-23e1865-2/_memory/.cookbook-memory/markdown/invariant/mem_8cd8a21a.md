---
type: Invariant
title: mem_8cd8a21a
description: 'In SymPy, `NDimArray.__len__` returns `self._loop_size`, the product of all shape dimensions.'
resource: 'memeval://memory/mem_8cd8a21a'
tags:
- SymPy
- tensor-array
- rank-0-array
timestamp: '2026-06-27T20:39:19.837473+00:00'
x_item_id: mem_8cd8a21a
x_relevancy: 0.85
x_version: 1
x_session_id: e7caa264-265e-4ba5-82f3-e0936a9fb7ad
x_source: daydream
x_tokens: 45
---

In SymPy, `NDimArray.__len__` returns `self._loop_size`, the product of all shape dimensions. For rank-0 arrays (shape=()), this must equal 1 to match the actual element count, not 0.
