---
type: Fix
title: mem_7e30b1c9
description: 'When splitting symbol names into base + subscript digits for pretty printing, use a regex like `[^\W\d_]+` (with `re.U`) instead of `[a-zA-Z]+` to handle Greek and other non-ASCII letters.'
resource: 'memeval://memory/mem_7e30b1c9'
tags:
- sympy
- printing
- conventions
timestamp: '2026-06-27T09:05:59.545766+00:00'
x_item_id: mem_7e30b1c9
x_relevancy: 0.9
x_version: 1
x_session_id: e110c60f-1a75-40d7-8c8f-fb76f9c0055d
x_source: daydream
x_tokens: 47
---

When splitting symbol names into base + subscript digits for pretty printing, use a regex like `[^\W\d_]+` (with `re.U`) instead of `[a-zA-Z]+` to handle Greek and other non-ASCII letters.
