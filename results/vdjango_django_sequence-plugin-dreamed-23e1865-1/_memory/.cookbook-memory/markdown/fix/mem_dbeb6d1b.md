---
type: Fix
title: mem_dbeb6d1b
description: 'When modifying the early-return fast path in Collector.delete() for models with no dependencies, ensure the instance''s PK is set to None because the normal cleanup loop is bypassed.'
resource: 'memeval://memory/mem_dbeb6d1b'
tags:
- django
- deletion
- collector
- pk
timestamp: '2026-06-27T20:14:53.539745+00:00'
x_item_id: mem_dbeb6d1b
x_relevancy: 0.9
x_version: 1
x_session_id: d724ddbc-6b37-407c-a6af-c44966c0b8fc
x_source: daydream
x_tokens: 45
---

When modifying the early-return fast path in Collector.delete() for models with no dependencies, ensure the instance's PK is set to None because the normal cleanup loop is bypassed.
