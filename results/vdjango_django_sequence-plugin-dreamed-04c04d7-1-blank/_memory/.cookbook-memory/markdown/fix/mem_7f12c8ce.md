---
type: Fix
title: mem_7f12c8ce
description: When serializing a type/metaclass in a migration serializer, prefer __qualname__ over __name__ to preserve inner-class nesting.
resource: 'memeval://memory/mem_7f12c8ce'
timestamp: '2026-06-27T16:32:40.005216+00:00'
x_item_id: mem_7f12c8ce
x_relevancy: 0.95
x_version: 1
x_session_id: 08a97d60-a315-4bb5-97ac-a85101c8c975
x_source: daydream
x_tokens: 31
---

When serializing a type/metaclass in a migration serializer, prefer __qualname__ over __name__ to preserve inner-class nesting.
