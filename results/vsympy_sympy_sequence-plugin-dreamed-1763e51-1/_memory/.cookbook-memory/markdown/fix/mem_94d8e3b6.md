---
type: Fix
title: mem_94d8e3b6
description: When writing a SymPy printer (e.g.
resource: 'memeval://memory/mem_94d8e3b6'
tags:
- sympy-printing
- mathematica-printer
timestamp: '2026-06-27T04:54:56.756178+00:00'
x_item_id: mem_94d8e3b6
x_relevancy: 0.85
x_version: 1
x_session_id: 6ada85d6-7fa5-43a4-a403-f15c71487220
x_source: daydream
x_tokens: 45
---

When writing a SymPy printer (e.g. MCodePrinter), use `stringify(expr.args, ", ")` inside square brackets to produce Mathematica-compatible function-call syntax for any custom type.
