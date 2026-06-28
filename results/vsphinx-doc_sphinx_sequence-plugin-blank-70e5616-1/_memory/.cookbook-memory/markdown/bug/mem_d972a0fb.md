---
type: Bug
title: mem_d972a0fb
description: 'When adding a comma separator in a loop over elements where the element list may be empty, guard the corresponding `pop()` with a check that elements were actually added.'
resource: 'memeval://memory/mem_d972a0fb'
tags:
- sphinx
- python-domain
timestamp: '2026-06-27T23:32:39.249636+00:00'
x_item_id: mem_d972a0fb
x_relevancy: 0.9
x_version: 1
x_session_id: 5e0444d1-6cc7-4efd-b4d9-e4cda21ce16d
x_source: daydream
x_tokens: 42
---

When adding a comma separator in a loop over elements where the element list may be empty, guard the corresponding `pop()` with a check that elements were actually added.
