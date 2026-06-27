---
type: Bug
title: mem_acf58b30
description: 'When a SymPy printer subclass doesn''t override _print_set, _print_dict, or _print_frozenset, the emptyPrinter fallback calls str() which loses structure and doesn''t roundtrip via eval.'
resource: 'memeval://memory/mem_acf58b30'
tags:
- sympy
- printing
- collections
timestamp: '2026-06-27T06:55:55.091629+00:00'
x_item_id: mem_acf58b30
x_relevancy: 0.9
x_version: 1
x_session_id: 42a0f35e-8d40-401c-a6a1-27228d96ce86
x_source: daydream
x_tokens: 46
---

When a SymPy printer subclass doesn't override _print_set, _print_dict, or _print_frozenset, the emptyPrinter fallback calls str() which loses structure and doesn't roundtrip via eval.
