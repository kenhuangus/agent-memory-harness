---
type: Fix
title: mem_dc8424d8
description: 'When using Python''s fcntl.flock() for file locking, it returns None on success and raises OSError on failure — use try/except to return True/False, not compare return value to 0.'
resource: 'memeval://memory/mem_dc8424d8'
tags:
- python
- fcntl
- file-locking
timestamp: '2026-06-28T02:07:02.113927+00:00'
x_item_id: mem_dc8424d8
x_relevancy: 0.95
x_version: 1
x_session_id: 9cf7999a-903c-430d-abab-9f02cdca0ce3
x_source: daydream
x_tokens: 44
---

When using Python's fcntl.flock() for file locking, it returns None on success and raises OSError on failure — use try/except to return True/False, not compare return value to 0.
