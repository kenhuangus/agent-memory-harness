---
type: Fix
title: mem_f5eac2f2
description: 'When implementing __reversed__() on a dict-backed ordered collection, delegate to reversed(self.dict) to match __iter__''s pattern and maintain insertion-order consistency.'
resource: 'memeval://memory/mem_f5eac2f2'
tags:
- django
- datastructures
- orderedset
timestamp: '2026-06-27T00:22:12.310564+00:00'
x_item_id: mem_f5eac2f2
x_relevancy: 0.9
x_version: 1
x_session_id: e05dc257-741b-4d3b-84b8-d1134da2cd9f
x_source: daydream
x_tokens: 42
---

When implementing __reversed__() on a dict-backed ordered collection, delegate to reversed(self.dict) to match __iter__'s pattern and maintain insertion-order consistency.
