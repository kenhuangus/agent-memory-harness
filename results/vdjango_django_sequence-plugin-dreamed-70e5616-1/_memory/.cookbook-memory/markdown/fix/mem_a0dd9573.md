---
type: Fix
title: mem_a0dd9573
description: When implementing a Django authentication backend, return None early when required credentials (username or password) are None to avoid unnecessary database queries and expensive password hashing.
resource: 'memeval://memory/mem_a0dd9573'
tags:
- django-auth
- performance
timestamp: '2026-06-28T00:27:55.363964+00:00'
x_item_id: mem_a0dd9573
x_relevancy: 0.85
x_version: 1
x_session_id: c6b505d9-8ac0-4196-baac-e6309b9706cc
x_source: daydream
x_tokens: 49
---

When implementing a Django authentication backend, return None early when required credentials (username or password) are None to avoid unnecessary database queries and expensive password hashing.
