---
type: Fix
title: mem_5dbabb8f
description: 'When performing a numeric comparison like `rv.exp < 0` on a SymPy object, guard with `rv.exp.is_real` first to avoid TypeError on complex-valued exponents.'
resource: 'memeval://memory/mem_5dbabb8f'
tags:
- simplify
timestamp: '2026-06-27T08:48:12.140672+00:00'
x_item_id: mem_5dbabb8f
x_relevancy: 0.95
x_version: 1
x_session_id: 882f0ccc-ea67-45ae-9af5-ff6c40924616
x_source: daydream
x_tokens: 38
---

When performing a numeric comparison like `rv.exp < 0` on a SymPy object, guard with `rv.exp.is_real` first to avoid TypeError on complex-valued exponents.
