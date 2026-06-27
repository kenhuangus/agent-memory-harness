---
type: Fix
title: mem_9e66db18
description: When a printer setting (like mul_symbol) is validated via dict lookup but downstream just uses the value as a string, fall back to pass-through on KeyError instead of rejecting arbitrary values.
resource: 'memeval://memory/mem_9e66db18'
timestamp: '2026-06-27T17:15:36.136276+00:00'
x_item_id: mem_9e66db18
x_relevancy: 0.9
x_version: 1
x_session_id: 5b8650c0-b9af-4e4e-8508-9b7b9773fd49
x_source: daydream
x_tokens: 48
---

When a printer setting (like mul_symbol) is validated via dict lookup but downstream just uses the value as a string, fall back to pass-through on KeyError instead of rejecting arbitrary values.
