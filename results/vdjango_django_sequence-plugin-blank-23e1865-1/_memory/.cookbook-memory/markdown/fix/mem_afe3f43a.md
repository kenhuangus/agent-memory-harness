---
type: Fix
title: mem_afe3f43a
description: 'When adding DISTINCT support to a Django aggregate subclass, set `allow_distinct = True` on the class; the base `Aggregate.__init__` and `as_sql` already handle the rest.'
resource: 'memeval://memory/mem_afe3f43a'
tags:
- django
- aggregates
- distinct
timestamp: '2026-06-27T22:34:39.976428+00:00'
x_item_id: mem_afe3f43a
x_relevancy: 0.9
x_version: 1
x_session_id: 9354c619-436a-4444-a707-5f7fa95a007b
x_source: daydream
x_tokens: 42
---

When adding DISTINCT support to a Django aggregate subclass, set `allow_distinct = True` on the class; the base `Aggregate.__init__` and `as_sql` already handle the rest.
