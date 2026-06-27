---
type: Fix
title: mem_9752acdf
description: 'When validating variable-length arguments with a fallback default, put the fallback in an explicit ''if not args:'' branch, not in the else of a condition that also tests validity.'
resource: 'memeval://memory/mem_9752acdf'
tags:
- sympy
- polys
- bug pattern
timestamp: '2026-06-27T16:45:31.519513+00:00'
x_item_id: mem_9752acdf
x_relevancy: 0.8
x_version: 1
x_session_id: 0e9a8490-83f0-46dc-bde7-b2570adf1799
x_source: daydream
x_tokens: 44
---

When validating variable-length arguments with a fallback default, put the fallback in an explicit 'if not args:' branch, not in the else of a condition that also tests validity.
