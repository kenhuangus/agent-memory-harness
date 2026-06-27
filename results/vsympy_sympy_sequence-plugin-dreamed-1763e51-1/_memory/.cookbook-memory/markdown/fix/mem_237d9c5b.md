---
type: Fix
title: mem_237d9c5b
description: 'When a monomial generator short-circuits on `max_degree == 0` and unconditionally yields `S.One`, it must guard with `if min_degree <= 0:` to respect non-zero lower bounds.'
resource: 'memeval://memory/mem_237d9c5b'
timestamp: '2026-06-27T05:46:55.892780+00:00'
x_item_id: mem_237d9c5b
x_relevancy: 1.0
x_version: 1
x_session_id: bb1b50bd-f6c2-4a73-8e0d-e515aef80dcf
x_source: daydream
x_tokens: 43
---

When a monomial generator short-circuits on `max_degree == 0` and unconditionally yields `S.One`, it must guard with `if min_degree <= 0:` to respect non-zero lower bounds.
