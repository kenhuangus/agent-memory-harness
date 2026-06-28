---
type: Invariant
title: mem_6e03aab6
description: 'In SymPy''s TensorProduct.flatten, `Mul._from_args([])` returns `1` for args with no non-commutative factors; this `1` was erroneously kept in nc_parts, creating needless TensorProduct wrapping.'
resource: 'memeval://memory/mem_6e03aab6'
tags:
- sympy
- quantum
- internals
timestamp: '2026-06-27T18:26:43.227573+00:00'
x_item_id: mem_6e03aab6
x_relevancy: 0.8
x_version: 1
x_session_id: 1f0e88c5-efb6-4edb-91c1-3674a16633ab
x_source: daydream
x_tokens: 48
---

In SymPy's TensorProduct.flatten, `Mul._from_args([])` returns `1` for args with no non-commutative factors; this `1` was erroneously kept in nc_parts, creating needless TensorProduct wrapping.
