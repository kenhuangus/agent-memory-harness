---
type: Fix
title: mem_b7eeee25
description: When deciding insert-vs-update in model save logic, check whether the pk was explicitly provided by the user (not just generated from a default) before forcing INSERT.
resource: 'memeval://memory/mem_b7eeee25'
timestamp: '2026-06-27T16:39:31.439737+00:00'
x_item_id: mem_b7eeee25
x_relevancy: 1.0
x_version: 1
x_session_id: e4b02cd2-b83c-48b9-9da8-c5ddc9453718
x_source: daydream
x_tokens: 41
---

When deciding insert-vs-update in model save logic, check whether the pk was explicitly provided by the user (not just generated from a default) before forcing INSERT.
