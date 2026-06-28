---
type: Fix
title: mem_584ddd5a
description: 'When a widget''s get_context() modifies its attrs dict, it can leak state to subsequent widgets in a multi-widget; always create a new dict instead of mutating the input.'
resource: 'memeval://memory/mem_584ddd5a'
tags:
- django
- forms
- widgets
timestamp: '2026-06-27T08:48:01.373294+00:00'
x_item_id: mem_584ddd5a
x_relevancy: 0.9
x_version: 1
x_session_id: f57f5ad1-8154-48da-bb0d-a446843f663b
x_source: daydream
x_tokens: 42
---

When a widget's get_context() modifies its attrs dict, it can leak state to subsequent widgets in a multi-widget; always create a new dict instead of mutating the input.
