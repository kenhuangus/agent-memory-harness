---
type: Fix
title: mem_50af6d4a
description: 'When a URL validator regex uses `\S` for the user:password part, restrict to `[^\s:@/]` to reject unencoded `:`, `@`, `/` per RFC 1738.'
resource: 'memeval://memory/mem_50af6d4a'
tags:
- django
- url-validation
- regex
timestamp: '2026-06-26T22:17:50.788314+00:00'
x_item_id: mem_50af6d4a
x_relevancy: 0.9
x_version: 1
x_session_id: 988dc5ab-c232-4d10-8650-e1d124ede38f
x_source: daydream
x_tokens: 33
---

When a URL validator regex uses `\S` for the user:password part, restrict to `[^\s:@/]` to reject unencoded `:`, `@`, `/` per RFC 1738.
