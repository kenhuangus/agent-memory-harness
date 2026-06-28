---
type: Workaround
title: mem_321d92ad
description: When using Mul(base, base) to expand Pow(TensorProduct, 2), always pass evaluate=False to prevent SymPy from re-converting the product back to a Pow, which would cause infinite recursion.
resource: 'memeval://memory/mem_321d92ad'
tags:
- workaround
- quantum
- tensor_product_simp
timestamp: '2026-06-28T01:44:23.761871+00:00'
x_item_id: mem_321d92ad
x_relevancy: 0.76
x_version: 1
x_session_id: 9160f454-9379-4121-8a4f-6079ae35b928
x_source: daydream
x_tokens: 46
---

When using Mul(base, base) to expand Pow(TensorProduct, 2), always pass evaluate=False to prevent SymPy from re-converting the product back to a Pow, which would cause infinite recursion.
