---
type: Fix
title: mem_ebd4439d
description: When calling Model.save() inside a transaction.atomic(using=X), also pass using=X to save() to avoid hitting the default database instead of the intended one.
resource: 'memeval://memory/mem_ebd4439d'
tags:
- database
- multi-db
timestamp: '2026-06-27T23:54:37.892012+00:00'
x_item_id: mem_ebd4439d
x_relevancy: 0.95
x_version: 1
x_session_id: 58a8e822-0307-468b-ad53-106fe2b93b38
x_source: daydream
x_tokens: 39
---

When calling Model.save() inside a transaction.atomic(using=X), also pass using=X to save() to avoid hitting the default database instead of the intended one.
