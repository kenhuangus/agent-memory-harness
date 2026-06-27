---
type: Fix
title: mem_e9eafb70
description: When serializing type objects in Django migration writers, use __qualname__ instead of __name__ to correctly preserve inner/nested class paths.
resource: 'memeval://memory/mem_e9eafb70'
tags:
- django
- migrations
- serialization
timestamp: '2026-06-26T23:09:19.302136+00:00'
x_item_id: mem_e9eafb70
x_relevancy: 0.95
x_version: 1
x_session_id: c56cd411-ea76-48f6-9b5c-c90b1919c6a5
x_source: daydream
x_tokens: 35
---

When serializing type objects in Django migration writers, use __qualname__ instead of __name__ to correctly preserve inner/nested class paths.
