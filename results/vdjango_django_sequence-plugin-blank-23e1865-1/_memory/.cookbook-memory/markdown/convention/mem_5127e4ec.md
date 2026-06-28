---
type: Convention
title: mem_5127e4ec
description: 'When passing attrs dicts into widget methods across a loop, pass a copy per invocation (e.g., `{**final_attrs, ''id'': ...}`) to avoid cross-iteration pollution from methods that mutate the dict.'
resource: 'memeval://memory/mem_5127e4ec'
timestamp: '2026-06-27T22:50:58.050496+00:00'
x_item_id: mem_5127e4ec
x_relevancy: 1.0
x_version: 1
x_session_id: 6d91e3f4-592c-4f2d-bc99-bdf1085c0d8b
x_source: daydream
x_tokens: 48
---

When passing attrs dicts into widget methods across a loop, pass a copy per invocation (e.g., `{**final_attrs, 'id': ...}`) to avoid cross-iteration pollution from methods that mutate the dict.
