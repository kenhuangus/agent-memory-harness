---
type: Fix
title: mem_ee3957e1
description: When serializing a nested class reference in a migration, use __qualname__ instead of __name__ to preserve the full dotted path including enclosing classes.
resource: 'memeval://memory/mem_ee3957e1'
tags:
- django
- migrations
- serialization
timestamp: '2026-06-27T08:46:20.825116+00:00'
x_item_id: mem_ee3957e1
x_relevancy: 0.95
x_version: 1
x_session_id: ecd4a0b7-ecb1-4654-a3f4-39c221e2756c
x_source: daydream
x_tokens: 39
---

When serializing a nested class reference in a migration, use __qualname__ instead of __name__ to preserve the full dotted path including enclosing classes.
