---
type: Memory
title: mem_f3915a8c
description: 'PostgreSQL''s BinaryField returns memoryview objects, not bytes, which causes HttpResponse to return the string ''<memory at 0x...>'' instead of the actual byte content.'
resource: 'memeval://memory/mem_f3915a8c'
tags:
- bug-behavior
- django
- database
timestamp: '2026-06-27T04:45:42.804305+00:00'
x_item_id: mem_f3915a8c
x_relevancy: 1.0
x_version: 1
x_session_id: 1cec7419-b3ff-45b7-8fda-4e3d4d3c9048
x_source: daydream
x_tokens: 41
---

PostgreSQL's BinaryField returns memoryview objects, not bytes, which causes HttpResponse to return the string '<memory at 0x...>' instead of the actual byte content.
