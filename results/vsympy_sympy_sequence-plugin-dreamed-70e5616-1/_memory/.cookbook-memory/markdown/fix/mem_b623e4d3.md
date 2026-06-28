---
type: Fix
title: mem_b623e4d3
description: 'When __ne__ is defined as ''not self.__eq__(other)'', guard against NotImplemented: check ''if result is NotImplemented: return NotImplemented'' before negating.'
resource: 'memeval://memory/mem_b623e4d3'
tags:
- python
- dunder-protocol
timestamp: '2026-06-28T01:03:39.924180+00:00'
x_item_id: mem_b623e4d3
x_relevancy: 0.9
x_version: 1
x_session_id: 82409708-0a0c-4ef3-881c-97555841df03
x_source: daydream
x_tokens: 39
---

When __ne__ is defined as 'not self.__eq__(other)', guard against NotImplemented: check 'if result is NotImplemented: return NotImplemented' before negating.
