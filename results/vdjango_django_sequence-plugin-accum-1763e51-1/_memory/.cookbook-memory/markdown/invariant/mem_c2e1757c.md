---
type: Invariant
title: mem_c2e1757c
description: When wrapping fcntl.flock() in Python, never compare its return value to 0 because it returns None on success and raises OSError on failure; always use try-except to determine success/failure.
resource: 'memeval://memory/mem_c2e1757c'
tags:
- django
- fcntl
- file-locking
timestamp: '2026-06-27T00:05:02.125709+00:00'
x_item_id: mem_c2e1757c
x_relevancy: 1.0
x_version: 1
x_session_id: 807ba051-928c-44b5-80e6-fab831a08055
x_source: daydream
x_tokens: 48
---

When wrapping fcntl.flock() in Python, never compare its return value to 0 because it returns None on success and raises OSError on failure; always use try-except to determine success/failure.
