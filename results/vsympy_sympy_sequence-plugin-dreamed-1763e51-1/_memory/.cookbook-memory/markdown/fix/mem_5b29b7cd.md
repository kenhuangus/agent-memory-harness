---
type: Fix
title: mem_5b29b7cd
description: When minimal_blocks() deletes entries from parallel lists while iterating, collect indices to delete and remove them in reverse order to avoid index shifting that causes IndexError.
resource: 'memeval://memory/mem_5b29b7cd'
tags:
- perm_groups
- minimal_blocks
- iteration-bug
timestamp: '2026-06-27T05:35:43.340543+00:00'
x_item_id: mem_5b29b7cd
x_relevancy: 1.0
x_version: 1
x_session_id: a3ba4142-1a6c-4002-b7ba-1aaedc2a0ed6
x_source: daydream
x_tokens: 45
---

When minimal_blocks() deletes entries from parallel lists while iterating, collect indices to delete and remove them in reverse order to avoid index shifting that causes IndexError.
