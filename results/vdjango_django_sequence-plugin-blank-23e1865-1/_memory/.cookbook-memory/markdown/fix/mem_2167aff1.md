---
type: Fix
title: mem_2167aff1
description: When working with fcntl.flock() or other POSIX system-call wrappers, use try/except OSError for success detection since these functions return None (not 0) on success and raise OSError on failure.
resource: 'memeval://memory/mem_2167aff1'
tags:
- python
- file-locking
- posix
timestamp: '2026-06-27T23:37:40.409270+00:00'
x_item_id: mem_2167aff1
x_relevancy: 1.0
x_version: 1
x_session_id: cf9e4511-7ad6-4367-b0b9-9648eda49543
x_source: daydream
x_tokens: 49
---

When working with fcntl.flock() or other POSIX system-call wrappers, use try/except OSError for success detection since these functions return None (not 0) on success and raise OSError on failure.
