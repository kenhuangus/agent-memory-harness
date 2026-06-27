---
type: Fix
title: mem_1fc4852f
description: 'In SymPy NumberSymbol comparison methods, ensure reflected calls use the correct symmetric operator: `__gt__` calls `other.__lt__(self)`, `__le__` calls `other.__ge__(self)`, etc., for symmetry.'
resource: 'memeval://memory/mem_1fc4852f'
tags:
- sympy
- number symbol
- comparison
timestamp: '2026-06-27T05:56:37.961326+00:00'
x_item_id: mem_1fc4852f
x_relevancy: 0.8
x_version: 1
x_session_id: d075c943-615a-414c-9bf2-5353af51881b
x_source: daydream
x_tokens: 48
---

In SymPy NumberSymbol comparison methods, ensure reflected calls use the correct symmetric operator: `__gt__` calls `other.__lt__(self)`, `__le__` calls `other.__ge__(self)`, etc., for symmetry.
