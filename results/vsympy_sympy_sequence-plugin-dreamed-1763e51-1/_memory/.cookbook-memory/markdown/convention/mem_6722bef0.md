---
type: Convention
title: mem_6722bef0
description: In SymPy, to avoid circular imports in __mul__ implementations that need classes from other modules (e.g., IdentityOperator), import them inside the method body rather than at module top level.
resource: 'memeval://memory/mem_6722bef0'
tags:
- sympy
- convention
- imports
timestamp: '2026-06-27T10:59:50.982991+00:00'
x_item_id: mem_6722bef0
x_relevancy: 0.9
x_version: 1
x_session_id: 727881dc-f3d6-48ab-99d3-0da6750f96bb
x_source: daydream
x_tokens: 48
---

In SymPy, to avoid circular imports in __mul__ implementations that need classes from other modules (e.g., IdentityOperator), import them inside the method body rather than at module top level.
