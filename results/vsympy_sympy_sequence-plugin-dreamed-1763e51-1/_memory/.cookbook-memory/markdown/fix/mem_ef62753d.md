---
type: Fix
title: mem_ef62753d
description: 'When `diophantine()` is called with `permute=True` and `syms` reorders variables, the recursive call must forward `permute=` or permutations are silently dropped.'
resource: 'memeval://memory/mem_ef62753d'
timestamp: '2026-06-27T05:23:49.555766+00:00'
x_item_id: mem_ef62753d
x_relevancy: 1.0
x_version: 1
x_session_id: f871ca4f-5a58-4977-98a7-aa02afe9a202
x_source: daydream
x_tokens: 40
---

When `diophantine()` is called with `permute=True` and `syms` reorders variables, the recursive call must forward `permute=` or permutations are silently dropped.
