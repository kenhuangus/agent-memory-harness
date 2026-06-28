---
type: Fix
title: mem_47ba860c
description: When iterating over parallel lists to remove elements, do not delete during the loop because indices shift; collect indices to remove and delete after the loop.
resource: 'memeval://memory/mem_47ba860c'
timestamp: '2026-06-27T05:30:01.314759+00:00'
x_item_id: mem_47ba860c
x_relevancy: 0.9
x_version: 1
x_session_id: aaca94ba-d2dd-4d8a-a28f-3c4d98f2ceeb
x_source: daydream
x_tokens: 40
---

When iterating over parallel lists to remove elements, do not delete during the loop because indices shift; collect indices to remove and delete after the loop.
