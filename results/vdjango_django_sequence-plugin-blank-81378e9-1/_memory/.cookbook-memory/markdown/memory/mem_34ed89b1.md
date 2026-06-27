---
type: Memory
title: mem_34ed89b1
description: The fast-delete optimization path in Collector.delete() skips post_delete signals and does not process instances through self.data, so PK clearing must be added explicitly before the early return.
resource: 'memeval://memory/mem_34ed89b1'
tags:
- Django
- ORM
- deletion
timestamp: '2026-06-27T04:51:49.637794+00:00'
x_item_id: mem_34ed89b1
x_relevancy: 0.88
x_version: 1
x_session_id: ac4d6aa9-3c83-451b-9ce9-032f661f104d
x_source: daydream
x_tokens: 49
---

The fast-delete optimization path in Collector.delete() skips post_delete signals and does not process instances through self.data, so PK clearing must be added explicitly before the early return.
