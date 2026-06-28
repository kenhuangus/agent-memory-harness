---
type: Fix
title: mem_d0ee2cd9
description: 'When a widget''s get_context() needs to add attributes, copy the input dict instead of mutating it, otherwise the leak breaks widgets that reuse an attrs dict across subwidgets (e.g.'
resource: 'memeval://memory/mem_d0ee2cd9'
tags:
- django
- forms
- widgets
timestamp: '2026-06-27T20:46:43.334100+00:00'
x_item_id: mem_d0ee2cd9
x_relevancy: 0.9
x_version: 1
x_session_id: 2088aa1e-f2c7-4e28-a154-8fb5ea0ebbe6
x_source: daydream
x_tokens: 50
---

When a widget's get_context() needs to add attributes, copy the input dict instead of mutating it, otherwise the leak breaks widgets that reuse an attrs dict across subwidgets (e.g. SplitArrayWidget).
