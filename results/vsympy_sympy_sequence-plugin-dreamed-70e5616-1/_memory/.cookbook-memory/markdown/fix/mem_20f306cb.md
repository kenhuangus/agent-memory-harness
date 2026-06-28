---
type: Fix
title: mem_20f306cb
description: 'When calling `cancel(ret)` on a symbolic matrix entry during Bareiss recursion, the result must be assigned back (`ret = cancel(ret)`); otherwise the canceled value is discarded.'
resource: 'memeval://memory/mem_20f306cb'
tags:
- cancel
- bareiss
- assignment
- bug
timestamp: '2026-06-27T21:34:35.208065+00:00'
x_item_id: mem_20f306cb
x_relevancy: 0.85
x_version: 1
x_session_id: bb5aab46-df36-416b-9e63-78ac275c2b00
x_source: daydream
x_tokens: 44
---

When calling `cancel(ret)` on a symbolic matrix entry during Bareiss recursion, the result must be assigned back (`ret = cancel(ret)`); otherwise the canceled value is discarded.
