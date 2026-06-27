---
type: Bug
title: mem_5baf8c36
description: When Meta.ordering contains an F() or other expression (not an OrderBy), get_order_dir() crashes because it expects a string; expression objects are not subscriptable.
resource: 'memeval://memory/mem_5baf8c36'
tags:
- django
- orm
- bug
- ordering
timestamp: '2026-06-27T08:33:57.223503+00:00'
x_item_id: mem_5baf8c36
x_relevancy: 0.85
x_version: 1
x_session_id: 8f776473-bf83-4d8b-b76d-14f390e8d7d0
x_source: daydream
x_tokens: 41
---

When Meta.ordering contains an F() or other expression (not an OrderBy), get_order_dir() crashes because it expects a string; expression objects are not subscriptable.
