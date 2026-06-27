---
type: Fix
title: mem_1cdaac1f
description: 'When building SQL PRAGMA statements with dynamic table/column names, always quote identifiers via the backend''s quote_name() to avoid syntax errors on SQL reserved words.'
resource: 'memeval://memory/mem_1cdaac1f'
tags:
- django
- sqlite
- database-backend
timestamp: '2026-06-27T06:37:52.001163+00:00'
x_item_id: mem_1cdaac1f
x_relevancy: 0.95
x_version: 1
x_session_id: 8b8f7381-16eb-48ac-ab43-05cd8c0e5204
x_source: daydream
x_tokens: 42
---

When building SQL PRAGMA statements with dynamic table/column names, always quote identifiers via the backend's quote_name() to avoid syntax errors on SQL reserved words.
