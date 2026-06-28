---
type: Fix
title: mem_7aeeefd0
description: 'When using Python''s `fcntl.flock()` for file locking, it returns `None` on success and raises `OSError` on failure – use try/except to return True/False, not compare return value to 0.'
resource: 'memeval://memory/mem_7aeeefd0'
tags:
- django
- posix
- file-locking
timestamp: '2026-06-27T10:00:14.765124+00:00'
x_item_id: mem_7aeeefd0
x_relevancy: 0.9
x_version: 1
x_session_id: 7e2a8f87-ab50-4a73-9b4c-fe79bdb02a86
x_source: daydream
x_tokens: 46
---

When using Python's `fcntl.flock()` for file locking, it returns `None` on success and raises `OSError` on failure – use try/except to return True/False, not compare return value to 0.
