---
type: Memory
title: mem_be41d961
description: 'The bug affected all Django versions going back to 1.11: `Field.__deepcopy__` performed a shallow copy, causing all deep-copied instances to share the same `error_messages` dictionary.'
resource: 'memeval://memory/mem_be41d961'
tags:
- bug-history
- durability
timestamp: '2026-06-27T12:22:02.450185+00:00'
x_item_id: mem_be41d961
x_relevancy: 0.85
x_version: 1
x_session_id: 65d17510-c11c-40cb-b2f2-c40ee0734efb
x_source: daydream
x_tokens: 46
---

The bug affected all Django versions going back to 1.11: `Field.__deepcopy__` performed a shallow copy, causing all deep-copied instances to share the same `error_messages` dictionary.
