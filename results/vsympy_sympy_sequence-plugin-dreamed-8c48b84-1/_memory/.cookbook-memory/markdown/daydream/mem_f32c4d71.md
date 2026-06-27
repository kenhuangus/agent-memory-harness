---
type: Memory
title: mem_f32c4d71
description: 'Calling `symbols((''q:2'', ''u:2''), cls=Function)` creates `Symbol` instances instead of `UndefinedFunction` instances because the recursive call to `symbols(name, **args)` omits the `cls` parameter.'
resource: 'memeval://memory/mem_f32c4d71'
tags:
- symbols
- Function
- UndefinedFunction
- bug
timestamp: '2026-06-26T18:55:32.472315+00:00'
x_item_id: mem_f32c4d71
x_relevancy: 0.95
x_version: 1
x_session_id: f9eaa8b6-21a0-4c88-89e8-0bcb66a81a4d
x_source: daydream
x_tokens: 49
---

Calling `symbols(('q:2', 'u:2'), cls=Function)` creates `Symbol` instances instead of `UndefinedFunction` instances because the recursive call to `symbols(name, **args)` omits the `cls` parameter.
