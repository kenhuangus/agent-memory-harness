---
type: Bug
title: mem_5cdc8c60
description: 'Python''s zip stops at the shortest iterable without warning, so operations on coordinate sequences of different lengths silently discard extra dimensions instead of treating them as zero.'
resource: 'memeval://memory/mem_5cdc8c60'
tags:
- geometry
- zip
- silent_data_loss
- dimension
timestamp: '2026-06-27T17:27:03.221058+00:00'
x_item_id: mem_5cdc8c60
x_relevancy: 0.9
x_version: 1
x_session_id: 09bbe5d7-9492-4847-9e93-86dd42d98560
x_source: daydream
x_tokens: 46
---

Python's zip stops at the shortest iterable without warning, so operations on coordinate sequences of different lengths silently discard extra dimensions instead of treating them as zero.
