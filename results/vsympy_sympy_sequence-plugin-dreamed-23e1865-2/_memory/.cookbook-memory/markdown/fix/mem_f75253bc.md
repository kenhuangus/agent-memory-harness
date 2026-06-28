---
type: Fix
title: mem_f75253bc
description: 'When SymPy TensorProduct receives only scalar/commutative args, the flatten method must move all nc_parts that are just `1` to c_part so `len(new_args)==0` and no TensorProduct is created.'
resource: 'memeval://memory/mem_f75253bc'
tags:
- sympy
- quantum
- tensorproduct
- simplification
timestamp: '2026-06-27T18:26:43.227573+00:00'
x_item_id: mem_f75253bc
x_relevancy: 0.9
x_version: 1
x_session_id: 1f0e88c5-efb6-4edb-91c1-3674a16633ab
x_source: daydream
x_tokens: 47
---

When SymPy TensorProduct receives only scalar/commutative args, the flatten method must move all nc_parts that are just `1` to c_part so `len(new_args)==0` and no TensorProduct is created.
