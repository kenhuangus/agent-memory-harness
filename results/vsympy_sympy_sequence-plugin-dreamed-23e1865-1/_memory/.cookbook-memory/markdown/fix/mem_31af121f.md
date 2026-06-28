---
type: Fix
title: mem_31af121f
description: When Add._eval_is_zero finds real parts cancel but pure imaginary terms exist, return None (undecided) not False, because imaginary parts may also cancel.
resource: 'memeval://memory/mem_31af121f'
tags:
- sympy
- core
timestamp: '2026-06-27T08:53:06.243193+00:00'
x_item_id: mem_31af121f
x_relevancy: 0.95
x_version: 1
x_session_id: b36c89cd-4d5f-4143-b2d1-775c76fed357
x_source: daydream
x_tokens: 38
---

When Add._eval_is_zero finds real parts cancel but pure imaginary terms exist, return None (undecided) not False, because imaginary parts may also cancel.
