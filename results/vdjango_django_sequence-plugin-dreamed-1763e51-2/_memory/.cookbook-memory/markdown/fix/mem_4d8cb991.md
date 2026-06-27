---
type: Fix
title: mem_4d8cb991
description: When adding an ordering-preserving collection that supports __iter__ via dict iteration, implement __reversed__ by delegating to reversed(self.dict) to maintain consistent insertion order semantics.
resource: 'memeval://memory/mem_4d8cb991'
tags:
- ordered-set
- datastructures
- django-utils
timestamp: '2026-06-27T10:23:08.384203+00:00'
x_item_id: mem_4d8cb991
x_relevancy: 0.95
x_version: 1
x_session_id: 30f8f376-13a6-467d-a2a7-67a2817d74b5
x_source: daydream
x_tokens: 49
---

When adding an ordering-preserving collection that supports __iter__ via dict iteration, implement __reversed__ by delegating to reversed(self.dict) to maintain consistent insertion order semantics.
