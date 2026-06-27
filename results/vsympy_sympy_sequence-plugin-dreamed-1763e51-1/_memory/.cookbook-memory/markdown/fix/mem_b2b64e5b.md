---
type: Fix
title: mem_b2b64e5b
description: When a generator yields a mutable dict/list that it mutates between iterations, yield a copy each time to prevent list(gen) from showing all entries as the same final object.
resource: 'memeval://memory/mem_b2b64e5b'
tags:
- python
- generator
- mutable-objects
- pitfall
timestamp: '2026-06-27T11:01:58.516920+00:00'
x_item_id: mem_b2b64e5b
x_relevancy: 0.9
x_version: 1
x_session_id: c65c78fb-19d1-4634-b7ea-91f95d780041
x_source: daydream
x_tokens: 43
---

When a generator yields a mutable dict/list that it mutates between iterations, yield a copy each time to prevent list(gen) from showing all entries as the same final object.
