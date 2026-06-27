---
type: Memory
title: mem_4ca75c9c
description: 'Python regex `$` matches before a trailing newline, so `^...$` can incorrectly accept strings ending with `\n`.'
resource: 'memeval://memory/mem_4ca75c9c'
tags:
- python
- regex
- validation
- security
timestamp: '2026-06-26T04:28:46.416309+00:00'
x_item_id: mem_4ca75c9c
x_relevancy: 0.9
x_version: 1
x_session_id: cd5eea48-8ba6-46e5-92fc-e0fa9642ba88
x_source: daydream
x_tokens: 41
---

Python regex `$` matches before a trailing newline, so `^...$` can incorrectly accept strings ending with `\n`. Use `\A` and `\Z` for strict string-boundary anchoring.
