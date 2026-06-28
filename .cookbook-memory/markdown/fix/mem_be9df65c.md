---
type: Fix
title: mem_be9df65c
description: 'When recovering Django test directives from selectors, the normalized label is a runnable runtests directive — keep it as-is and dedupe rather than slicing to parts[:-2] or requiring ≥3 segments.'
resource: 'memeval://memory/mem_be9df65c'
tags:
- python
- django
- swebench
- grading
timestamp: '2026-06-27T01:04:49.632677+00:00'
x_item_id: mem_be9df65c
x_relevancy: 0.8
x_version: 1
x_session_id: 4978a0de-65c2-4fea-91ca-9323568413f5
x_source: daydream
x_tokens: 48
---

When recovering Django test directives from selectors, the normalized label is a runnable runtests directive — keep it as-is and dedupe rather than slicing to parts[:-2] or requiring ≥3 segments.
