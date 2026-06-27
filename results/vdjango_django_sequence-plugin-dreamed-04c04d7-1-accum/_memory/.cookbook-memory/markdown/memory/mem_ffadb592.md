---
type: Memory
title: mem_ffadb592
description: 'The fix for clearing PK in the fast-delete path uses `setattr(instance, model._meta.pk.attname, None)` -- the same pattern used in the normal deletion path.'
resource: 'memeval://memory/mem_ffadb592'
tags:
- django
- deletion
- code-pattern
timestamp: '2026-06-27T11:44:55.634553+00:00'
x_item_id: mem_ffadb592
x_relevancy: 0.9
x_version: 1
x_session_id: ed5e7fe2-fb8a-41fd-8455-16b6486d7c61
x_source: daydream
x_tokens: 39
---

The fix for clearing PK in the fast-delete path uses `setattr(instance, model._meta.pk.attname, None)` -- the same pattern used in the normal deletion path.
