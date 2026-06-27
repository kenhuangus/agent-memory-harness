---
type: Fix
title: mem_7b5db363
description: 'When implementing or editing a URL regex that supports user:password authentication (RFC 1738), replace `\S+(?::\S*)?` with `[^\s:@/]+(?::[^\s:@/]*)?` to reject unencoded `:`, `@`, `/` in credentials.'
resource: 'memeval://memory/mem_7b5db363'
tags:
- 'django::validators'
- rfc1738
timestamp: '2026-06-27T04:40:13.482376+00:00'
x_item_id: mem_7b5db363
x_relevancy: 0.9
x_version: 1
x_session_id: ba81c605-5ea3-4eaa-ae23-afb04c8823f4
x_source: daydream
x_tokens: 50
---

When implementing or editing a URL regex that supports user:password authentication (RFC 1738), replace `\S+(?::\S*)?` with `[^\s:@/]+(?::[^\s:@/]*)?` to reject unencoded `:`, `@`, `/` in credentials.
