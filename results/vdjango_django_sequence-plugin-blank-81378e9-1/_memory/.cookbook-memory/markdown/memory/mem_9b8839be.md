---
type: Memory
title: mem_9b8839be
description: 'The fix for the composed query values_list mutation bug is to clone the compiler''s query before calling set_values() in get_combinator_sql() in django/db/models/sql/compiler.py, around lines 428-433.'
resource: 'memeval://memory/mem_9b8839be'
tags:
- fix
- django
- ORM
- SQL compilation
timestamp: '2026-06-27T05:12:41.628375+00:00'
x_item_id: mem_9b8839be
x_relevancy: 0.9
x_version: 1
x_session_id: c525d4df-ee7e-4e45-92e6-78213264a9fd
x_source: daydream
x_tokens: 49
---

The fix for the composed query values_list mutation bug is to clone the compiler's query before calling set_values() in get_combinator_sql() in django/db/models/sql/compiler.py, around lines 428-433.
