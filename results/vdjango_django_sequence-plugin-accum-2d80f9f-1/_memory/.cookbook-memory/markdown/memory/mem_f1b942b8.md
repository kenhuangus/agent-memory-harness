---
type: Memory
title: mem_f1b942b8
description: 'get_order_dir() in django/db/models/sql/query.py assumes its field argument is a string and calls field[0], which raises TypeError on OrderBy or F expression objects.'
resource: 'memeval://memory/mem_f1b942b8'
tags:
- django-orm
- ordering
- bug-pattern
timestamp: '2026-06-27T12:16:18.795936+00:00'
x_item_id: mem_f1b942b8
x_relevancy: 0.95
x_version: 1
x_session_id: ed491f42-5863-409b-b972-63ff95e74fce
x_source: daydream
x_tokens: 41
---

get_order_dir() in django/db/models/sql/query.py assumes its field argument is a string and calls field[0], which raises TypeError on OrderBy or F expression objects.
