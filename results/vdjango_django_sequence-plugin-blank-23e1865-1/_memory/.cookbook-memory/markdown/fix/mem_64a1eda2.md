---
type: Fix
title: mem_64a1eda2
description: 'When a format helper returns a numeric year without zero-padding, years < 1000 produce the wrong digit count; always format with ''%04d''.'
resource: 'memeval://memory/mem_64a1eda2'
tags:
- dateformat
- zero-pad
timestamp: '2026-06-28T00:05:19.746451+00:00'
x_item_id: mem_64a1eda2
x_relevancy: 0.85
x_version: 1
x_session_id: a1ead72c-e4eb-4c12-8f0b-91ff6601976d
x_source: daydream
x_tokens: 34
---

When a format helper returns a numeric year without zero-padding, years < 1000 produce the wrong digit count; always format with '%04d'.
