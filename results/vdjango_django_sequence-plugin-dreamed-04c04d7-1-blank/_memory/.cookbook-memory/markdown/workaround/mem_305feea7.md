---
type: Workaround
title: mem_305feea7
description: When a SimpleLazyObject is passed to db operations, call str() to trigger evaluation and extract _wrapped to get the concrete value; this emits any pending deprecation warnings as intended.
resource: 'memeval://memory/mem_305feea7'
tags:
- lazy evaluation
- database
timestamp: '2026-06-27T17:31:18.071334+00:00'
x_item_id: mem_305feea7
x_relevancy: 0.85
x_version: 1
x_session_id: b00866a8-cc0f-42c9-a850-35e691a8baa1
x_source: daydream
x_tokens: 47
---

When a SimpleLazyObject is passed to db operations, call str() to trigger evaluation and extract _wrapped to get the concrete value; this emits any pending deprecation warnings as intended.
