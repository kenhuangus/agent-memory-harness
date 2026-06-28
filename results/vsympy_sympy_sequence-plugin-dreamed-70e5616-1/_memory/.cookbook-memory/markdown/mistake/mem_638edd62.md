---
type: Mistake
title: mem_638edd62
description: When a local variable is assigned only inside an if-block and referenced unconditionally later, initialize it before the block or move the reference inside the block to prevent UnboundLocalError.
resource: 'memeval://memory/mem_638edd62'
tags:
- python
- debugging
- coding-pattern
timestamp: '2026-06-28T00:19:59.794803+00:00'
x_item_id: mem_638edd62
x_relevancy: 0.8
x_version: 1
x_session_id: e9989b94-2ca6-4c34-9965-4c73908e732a
x_source: daydream
x_tokens: 48
---

When a local variable is assigned only inside an if-block and referenced unconditionally later, initialize it before the block or move the reference inside the block to prevent UnboundLocalError.
