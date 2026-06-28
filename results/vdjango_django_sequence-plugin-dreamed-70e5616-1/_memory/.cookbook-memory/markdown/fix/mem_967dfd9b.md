---
type: Fix
title: mem_967dfd9b
description: 'When a Widget''s get_context() needs to modify attrs to reflect widget state (e.g., checked), copy the attrs dict before mutating to prevent state leakage across repeated renderings.'
resource: 'memeval://memory/mem_967dfd9b'
timestamp: '2026-06-28T01:06:26.268582+00:00'
x_item_id: mem_967dfd9b
x_relevancy: 0.95
x_version: 1
x_session_id: e0946a0b-d56f-4ab1-b412-b25dd495a15a
x_source: daydream
x_tokens: 45
---

When a Widget's get_context() needs to modify attrs to reflect widget state (e.g., checked), copy the attrs dict before mutating to prevent state leakage across repeated renderings.
