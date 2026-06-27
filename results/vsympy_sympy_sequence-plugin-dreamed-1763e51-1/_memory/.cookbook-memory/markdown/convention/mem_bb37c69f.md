---
type: Convention
title: mem_bb37c69f
description: 'When a printer subclass lacks a `_print_*` method for a SymPy type, it falls back to a ''Not supported'' comment.'
resource: 'memeval://memory/mem_bb37c69f'
tags:
- sympy
- printing
- convention
timestamp: '2026-06-27T08:45:16.568205+00:00'
x_item_id: mem_bb37c69f
x_relevancy: 1.0
x_version: 1
x_session_id: 5e142ebd-118e-4fcf-b347-85d85635def4
x_source: daydream
x_tokens: 43
---

When a printer subclass lacks a `_print_*` method for a SymPy type, it falls back to a 'Not supported' comment. Add `_print_ClassName(self, expr)` to the base printer to fix.
