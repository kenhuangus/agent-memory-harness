---
type: Fix
title: mem_503dc67d
description: 'When a simplification transformation compares an exponent using < or > operators, guard with `if not rv.exp.is_real: return rv` first because complex numbers do not support ordering in SymPy.'
resource: 'memeval://memory/mem_503dc67d'
tags:
- sympy
timestamp: '2026-06-27T16:17:45.596283+00:00'
x_item_id: mem_503dc67d
x_relevancy: 1.0
x_version: 1
x_session_id: 679209ea-d0e3-4153-aa72-399b1228edf8
x_source: daydream
x_tokens: 47
---

When a simplification transformation compares an exponent using < or > operators, guard with `if not rv.exp.is_real: return rv` first because complex numbers do not support ordering in SymPy.
