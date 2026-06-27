---
type: Fix
title: mem_0eaeead3
description: 'When a SymPy Mod.doit() handler for Mul checks whether non-mod args change after wrapping in Mod(x, q), it must compare before/after to detect reductions like 3*i -> i (mod 2).'
resource: 'memeval://memory/mem_0eaeead3'
tags:
- sympy
- mod
- simplification
timestamp: '2026-06-27T07:13:55.178577+00:00'
x_item_id: mem_0eaeead3
x_relevancy: 0.9
x_version: 1
x_session_id: 5241ecfd-cff9-443f-a24f-b7471965334c
x_source: daydream
x_tokens: 44
---

When a SymPy Mod.doit() handler for Mul checks whether non-mod args change after wrapping in Mod(x, q), it must compare before/after to detect reductions like 3*i -> i (mod 2).
