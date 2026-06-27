---
type: Fix
title: mem_904c7ca8
description: When implementing a reversible ordered collection in Python (e.g., OrderedSet), add __reversed__ that delegates to reversed(self.dict) to mirror __iter__ and preserve reverse insertion order.
resource: 'memeval://memory/mem_904c7ca8'
tags:
- python
- django
timestamp: '2026-06-27T06:47:05.019845+00:00'
x_item_id: mem_904c7ca8
x_relevancy: 0.9
x_version: 1
x_session_id: deca9b5e-7478-46b9-b9c3-2c4ccc9b1048
x_source: daydream
x_tokens: 47
---

When implementing a reversible ordered collection in Python (e.g., OrderedSet), add __reversed__ that delegates to reversed(self.dict) to mirror __iter__ and preserve reverse insertion order.
