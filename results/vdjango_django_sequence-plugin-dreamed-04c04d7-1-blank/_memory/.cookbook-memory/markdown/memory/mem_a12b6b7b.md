---
type: Memory
title: mem_a12b6b7b
description: 'PostgreSQL returns BinaryField data as `memoryview` objects, while SQLite returns them as `bytes`.'
resource: 'memeval://memory/mem_a12b6b7b'
tags:
- database
- behavior
timestamp: '2026-06-27T11:40:01.224651+00:00'
x_item_id: mem_a12b6b7b
x_relevancy: 0.85
x_version: 1
x_session_id: 748a619a-3335-4c07-b989-5e052906e35c
x_source: daydream
x_tokens: 38
---

PostgreSQL returns BinaryField data as `memoryview` objects, while SQLite returns them as `bytes`. This mismatch caused the bug described in ticket #11133.
