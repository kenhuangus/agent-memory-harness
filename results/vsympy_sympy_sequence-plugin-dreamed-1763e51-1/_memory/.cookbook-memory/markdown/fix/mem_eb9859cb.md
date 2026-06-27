---
type: Fix
title: mem_eb9859cb
description: When deleting items from parallel lists during iteration, collect indices first then delete in reverse order to avoid index invalidation.
resource: 'memeval://memory/mem_eb9859cb'
tags:
- perm_groups
- minimal_blocks
timestamp: '2026-06-27T09:03:07.779872+00:00'
x_item_id: mem_eb9859cb
x_relevancy: 0.9
x_version: 1
x_session_id: 1b68546c-c3f7-4d49-b1d7-be48242c55c6
x_source: daydream
x_tokens: 34
---

When deleting items from parallel lists during iteration, collect indices first then delete in reverse order to avoid index invalidation.
