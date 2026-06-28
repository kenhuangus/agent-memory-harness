---
type: Bug
title: mem_ff1f80d4
description: 'When a function uses variable-length unpacking `*args` and then indexes `args[0]`, the empty-call case raises IndexError; guard with an early return for the empty argument list.'
resource: 'memeval://memory/mem_ff1f80d4'
tags:
- sympy
- radsimp
- sqrtdenest
timestamp: '2026-06-28T00:27:27.937518+00:00'
x_item_id: mem_ff1f80d4
x_relevancy: 0.75
x_version: 1
x_session_id: 71fc7511-9d49-4fe0-b181-fc0f6dd3629f
x_source: daydream
x_tokens: 44
---

When a function uses variable-length unpacking `*args` and then indexes `args[0]`, the empty-call case raises IndexError; guard with an early return for the empty argument list.
