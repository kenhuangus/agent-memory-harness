---
type: Memory
title: mem_71feb3bd
description: 'Issue #12420: `sqrtdenest((3 - sqrt(2)*sqrt(4 + 3*I) + 3*I)/2)` previously raised IndexError; the fix ensures that expressions that cannot be denested are returned unchanged rather than crashing.'
resource: 'memeval://memory/mem_71feb3bd'
tags:
- issue
- sqrtdenest
- bug fix
timestamp: '2026-06-26T09:02:41.130785+00:00'
x_item_id: mem_71feb3bd
x_relevancy: 1.0
x_version: 1
x_session_id: a8de2180-f228-4843-a989-803293843a15
x_source: daydream
x_tokens: 48
---

Issue #12420: `sqrtdenest((3 - sqrt(2)*sqrt(4 + 3*I) + 3*I)/2)` previously raised IndexError; the fix ensures that expressions that cannot be denested are returned unchanged rather than crashing.
