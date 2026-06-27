---
type: Memory
title: mem_ed052c71
description: 'The fix for the `symbols()` cls bug is to change the recursive call from `symbols(name, **args)` to `symbols(name, cls=cls, **args)` in the else branch that handles non-string `names`.'
resource: 'memeval://memory/mem_ed052c71'
tags:
- symbols
- fix
- patch
timestamp: '2026-06-26T18:55:32.472315+00:00'
x_item_id: mem_ed052c71
x_relevancy: 0.9
x_version: 1
x_session_id: f9eaa8b6-21a0-4c88-89e8-0bcb66a81a4d
x_source: daydream
x_tokens: 46
---

The fix for the `symbols()` cls bug is to change the recursive call from `symbols(name, **args)` to `symbols(name, cls=cls, **args)` in the else branch that handles non-string `names`.
