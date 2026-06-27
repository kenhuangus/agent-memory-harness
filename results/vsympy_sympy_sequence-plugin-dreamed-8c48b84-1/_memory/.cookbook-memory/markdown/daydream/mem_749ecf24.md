---
type: Memory
title: mem_749ecf24
description: 'The `symbols()` function''s `cls` parameter is a keyword-only argument (defined after `*` in the signature), so it is not captured in `**args` and must be passed explicitly when recursing.'
resource: 'memeval://memory/mem_749ecf24'
tags:
- symbols
- cls
- bug-fix
timestamp: '2026-06-26T18:55:32.472315+00:00'
x_item_id: mem_749ecf24
x_relevancy: 0.95
x_version: 1
x_session_id: f9eaa8b6-21a0-4c88-89e8-0bcb66a81a4d
x_source: daydream
x_tokens: 46
---

The `symbols()` function's `cls` parameter is a keyword-only argument (defined after `*` in the signature), so it is not captured in `**args` and must be passed explicitly when recursing.
