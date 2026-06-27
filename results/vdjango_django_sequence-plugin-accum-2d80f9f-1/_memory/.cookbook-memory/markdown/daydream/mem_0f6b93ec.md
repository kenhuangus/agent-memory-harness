---
type: Memory
title: mem_0f6b93ec
description: 'The `BaseDatabaseOperations.execute_sql_flush()` method no longer accepts a `using` parameter; it now infers the database alias from `self.connection.alias`.'
resource: 'memeval://memory/mem_0f6b93ec'
tags:
- api-change
- refactoring
timestamp: '2026-06-26T17:14:52.476616+00:00'
x_item_id: mem_0f6b93ec
x_relevancy: 0.95
x_version: 1
x_session_id: 72b89eca-4ab8-4cd0-ba82-2e62371d73d8
x_source: daydream
x_tokens: 39
---

The `BaseDatabaseOperations.execute_sql_flush()` method no longer accepts a `using` parameter; it now infers the database alias from `self.connection.alias`.
