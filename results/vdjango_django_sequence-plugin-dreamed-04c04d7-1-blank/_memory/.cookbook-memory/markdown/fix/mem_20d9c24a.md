---
type: Fix
title: mem_20d9c24a
description: 'When using Python''s fcntl.flock() in the POSIX implementation, remember that it returns None on success and raises OSError on failure; never check the return value against 0.'
resource: 'memeval://memory/mem_20d9c24a'
tags:
- file-locking
- posix
- fcntl
timestamp: '2026-06-27T17:39:48.138060+00:00'
x_item_id: mem_20d9c24a
x_relevancy: 0.95
x_version: 1
x_session_id: b443e20a-5eed-4bbd-8ce8-f385424552a4
x_source: daydream
x_tokens: 43
---

When using Python's fcntl.flock() in the POSIX implementation, remember that it returns None on success and raises OSError on failure; never check the return value against 0.
