---
type: Invariant
title: mem_4b0ae180
description: 'In SymPy''s ReprPrinter, the output of _print_* methods must be valid Python that reconstructs the object via eval().'
resource: 'memeval://memory/mem_4b0ae180'
tags:
- sympy-printing
- repr-invariant
timestamp: '2026-06-27T17:54:25.998176+00:00'
x_item_id: mem_4b0ae180
x_relevancy: 0.8
x_version: 1
x_session_id: 17ce8097-3074-4f0a-9758-c9b0b7c4505b
x_source: daydream
x_tokens: 40
---

In SymPy's ReprPrinter, the output of _print_* methods must be valid Python that reconstructs the object via eval(). This is the key invariant for repr semantics.
