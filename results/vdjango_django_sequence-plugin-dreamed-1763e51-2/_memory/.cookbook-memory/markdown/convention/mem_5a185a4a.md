---
type: Convention
title: mem_5a185a4a
description: 'When implementing `__reversed__` on an ordered collection that uses a dict for ordering (Python 3.7+), delegate to `reversed(self.dict)` to match the existing `__iter__` delegation pattern.'
resource: 'memeval://memory/mem_5a185a4a'
tags:
- python
- ordered collection
- reverse iteration
timestamp: '2026-06-27T17:28:24.377417+00:00'
x_item_id: mem_5a185a4a
x_relevancy: 0.8
x_version: 1
x_session_id: 522cd003-0cdb-46a1-b029-7a95da03056c
x_source: daydream
x_tokens: 47
---

When implementing `__reversed__` on an ordered collection that uses a dict for ordering (Python 3.7+), delegate to `reversed(self.dict)` to match the existing `__iter__` delegation pattern.
