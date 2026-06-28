---
type: Fix
title: mem_f124b9e8
description: When serializing a type in Django migration serialization (TypeSerializer), use __qualname__ instead of __name__ to correctly resolve nested/inner class paths.
resource: 'memeval://memory/mem_f124b9e8'
tags:
- django
- serialization
- migrations
timestamp: '2026-06-27T16:17:24.688264+00:00'
x_item_id: mem_f124b9e8
x_relevancy: 0.9
x_version: 1
x_session_id: 7c46c3ff-bf1b-47bd-9854-7999afbc367d
x_source: daydream
x_tokens: 49
---

When serializing a type in Django migration serialization (TypeSerializer), use __qualname__ instead of __name__ to correctly resolve nested/inner class paths. Matches Enum/FunctionType serializers.
