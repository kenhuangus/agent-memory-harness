---
type: Memory
title: mem_62926cfb
description: 'Django''s SQLite introspection module consistently uses self.connection.ops.quote_name(table_name) when passing table names to PRAGMA table_info, PRAGMA foreign_key_list, and PRAGMA index_list.'
resource: 'memeval://memory/mem_62926cfb'
tags:
- Django
- SQLite
- convention
timestamp: '2026-06-27T14:15:58.979542+00:00'
x_item_id: mem_62926cfb
x_relevancy: 1.0
x_version: 1
x_session_id: 6a83d096-8d37-49d3-99c8-1f0930d0c699
x_source: daydream
x_tokens: 48
---

Django's SQLite introspection module consistently uses self.connection.ops.quote_name(table_name) when passing table names to PRAGMA table_info, PRAGMA foreign_key_list, and PRAGMA index_list.
