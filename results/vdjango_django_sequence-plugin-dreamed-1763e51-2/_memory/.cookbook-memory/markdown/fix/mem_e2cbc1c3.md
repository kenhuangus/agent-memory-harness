---
type: Fix
title: mem_e2cbc1c3
description: When calling cursor.fetchone() after a SQL query that may return zero rows, always guard with an if-check before subscripting the result.
resource: 'memeval://memory/mem_e2cbc1c3'
tags:
- database
- sql
- defensive-programming
timestamp: '2026-06-27T09:08:32.008354+00:00'
x_item_id: mem_e2cbc1c3
x_relevancy: 0.95
x_version: 1
x_session_id: 35b9680d-4c53-4eb9-9b01-3c39af9387c2
x_source: daydream
x_tokens: 34
---

When calling cursor.fetchone() after a SQL query that may return zero rows, always guard with an if-check before subscripting the result.
