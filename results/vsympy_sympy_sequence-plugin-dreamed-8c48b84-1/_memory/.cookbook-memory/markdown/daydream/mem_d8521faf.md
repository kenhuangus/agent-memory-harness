---
type: Memory
title: mem_d8521faf
description: 'Matrix.hstack with matrices having 0 rows and varying column counts returned incorrect shape; the fix in row_join returns `other` directly when `self` is a (0x0) matrix.'
resource: 'memeval://memory/mem_d8521faf'
tags:
- bug-fix
- matrix-operations
timestamp: '2026-06-26T19:24:25.393607+00:00'
x_item_id: mem_d8521faf
x_relevancy: 1.0
x_version: 1
x_session_id: 250cda64-da14-4e76-bbf0-d33817b33629
x_source: daydream
x_tokens: 42
---

Matrix.hstack with matrices having 0 rows and varying column counts returned incorrect shape; the fix in row_join returns `other` directly when `self` is a (0x0) matrix.
