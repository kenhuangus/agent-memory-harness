---
type: Strategy
title: mem_c69498ae
description: 'When fixing a generator that reuses mutable objects, search all callers and tests for workarounds (e.g., `.copy()` calls) and update them to use the generator directly.'
resource: 'memeval://memory/mem_c69498ae'
tags:
- refactoring
- testing
timestamp: '2026-06-28T02:56:38.132401+00:00'
x_item_id: mem_c69498ae
x_relevancy: 1.0
x_version: 1
x_session_id: 256f4abc-246e-48ca-a4ba-16f646c494fc
x_source: daydream
x_tokens: 42
---

When fixing a generator that reuses mutable objects, search all callers and tests for workarounds (e.g., `.copy()` calls) and update them to use the generator directly.
