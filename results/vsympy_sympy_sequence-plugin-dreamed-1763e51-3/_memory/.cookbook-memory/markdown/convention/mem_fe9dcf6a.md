---
type: Convention
title: mem_fe9dcf6a
description: 'When adding an else clause for safety in SymPy''s evalf code path, the correct explicit exception is NotImplementedError (not RuntimeError or ValueError) to match the surrounding pattern.'
resource: 'memeval://memory/mem_fe9dcf6a'
tags:
- convention
- evalf
- NotImplementedError
timestamp: '2026-06-27T04:47:35.480537+00:00'
x_item_id: mem_fe9dcf6a
x_relevancy: 0.7
x_version: 1
x_session_id: 48b74540-7e60-4226-a40c-9b824340c6a2
x_source: daydream
x_tokens: 46
---

When adding an else clause for safety in SymPy's evalf code path, the correct explicit exception is NotImplementedError (not RuntimeError or ValueError) to match the surrounding pattern.
