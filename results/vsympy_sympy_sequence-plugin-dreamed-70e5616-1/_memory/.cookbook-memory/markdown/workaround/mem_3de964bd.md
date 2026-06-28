---
type: Workaround
title: mem_3de964bd
description: In SymPy core modules (like relational.py), avoid top-level imports from sympy.sets to prevent circular imports; import ConditionSet locally inside the method.
resource: 'memeval://memory/mem_3de964bd'
tags:
- sympy-core
- import
timestamp: '2026-06-27T06:46:06.101866+00:00'
x_item_id: mem_3de964bd
x_relevancy: 0.7
x_version: 1
x_session_id: 3945a74a-7ee3-49bf-bd8b-b83c5799eedc
x_source: daydream
x_tokens: 39
---

In SymPy core modules (like relational.py), avoid top-level imports from sympy.sets to prevent circular imports; import ConditionSet locally inside the method.
