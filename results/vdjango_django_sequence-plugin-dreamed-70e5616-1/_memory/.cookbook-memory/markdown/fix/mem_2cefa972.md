---
type: Fix
title: mem_2cefa972
description: 'When a function parameter defaults to None but accepts a list, use ''if X is not None'' instead of ''if X'' to distinguish ''not given'' from ''empty list''.'
resource: 'memeval://memory/mem_2cefa972'
tags:
- Django
- forms
- models
timestamp: '2026-06-28T00:05:57.224357+00:00'
x_item_id: mem_2cefa972
x_relevancy: 0.95
x_version: 1
x_session_id: c09bf902-17ef-41e0-a1bd-58a617855122
x_source: daydream
x_tokens: 37
---

When a function parameter defaults to None but accepts a list, use 'if X is not None' instead of 'if X' to distinguish 'not given' from 'empty list'.
