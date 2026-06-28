---
type: Fix
title: mem_d46f39ec
description: 'When implementing __reversed__ on an ordered collection backed by a dict (Python 3.7+), delegate to reversed(self.dict) to mirror __iter__''s delegation pattern and preserve insertion order.'
resource: 'memeval://memory/mem_d46f39ec'
timestamp: '2026-06-28T02:42:32.646231+00:00'
x_item_id: mem_d46f39ec
x_relevancy: 1.0
x_version: 1
x_session_id: 41e1d1e7-7615-4247-a942-343301bf7ab0
x_source: daydream
x_tokens: 47
---

When implementing __reversed__ on an ordered collection backed by a dict (Python 3.7+), delegate to reversed(self.dict) to mirror __iter__'s delegation pattern and preserve insertion order.
