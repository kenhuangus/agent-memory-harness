---
type: Memory
title: mem_b280cbb0
description: 'Fix uses lazy dict creation: initialize env to None, then use ''env = env or {}'' before each conditional key assignment, so env stays None when no vars are set.'
resource: 'memeval://memory/mem_b280cbb0'
tags:
- python
- coding-pattern
timestamp: '2026-06-26T18:34:43.669942+00:00'
x_item_id: mem_b280cbb0
x_relevancy: 0.65
x_version: 1
x_session_id: d437124c-decf-4bdf-891d-9c520ef76e8f
x_source: daydream
x_tokens: 39
---

Fix uses lazy dict creation: initialize env to None, then use 'env = env or {}' before each conditional key assignment, so env stays None when no vars are set.
