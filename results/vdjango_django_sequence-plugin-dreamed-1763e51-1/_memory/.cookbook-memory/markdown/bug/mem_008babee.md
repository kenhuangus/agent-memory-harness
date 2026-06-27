---
type: Bug
title: mem_008babee
description: 'When a table name is a SQL reserved word (e.g., ''order''), PRAGMA foreign_key_check(table_name) fails with ''syntax error'' because SQLite requires quoted identifiers in PRAGMA arguments.'
resource: 'memeval://memory/mem_008babee'
tags:
- django
- sqlite
- database-backend
timestamp: '2026-06-27T06:37:52.001163+00:00'
x_item_id: mem_008babee
x_relevancy: 0.9
x_version: 1
x_session_id: 8b8f7381-16eb-48ac-ab43-05cd8c0e5204
x_source: daydream
x_tokens: 46
---

When a table name is a SQL reserved word (e.g., 'order'), PRAGMA foreign_key_check(table_name) fails with 'syntax error' because SQLite requires quoted identifiers in PRAGMA arguments.
