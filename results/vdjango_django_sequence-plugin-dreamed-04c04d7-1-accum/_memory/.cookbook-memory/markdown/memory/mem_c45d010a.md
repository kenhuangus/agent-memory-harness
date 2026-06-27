---
type: Memory
title: mem_c45d010a
description: 'In RenameContentType._rename(), content_type.save() must include ''using=db'' to save to the correct database; the database alias is obtained from schema_editor.connection.alias.'
resource: 'memeval://memory/mem_c45d010a'
tags:
- django
- contenttypes
- rename
- database
timestamp: '2026-06-27T11:32:20.385035+00:00'
x_item_id: mem_c45d010a
x_relevancy: 1.0
x_version: 1
x_session_id: 65b5b8ae-27bc-40b2-b2fd-726555b4e460
x_source: daydream
x_tokens: 44
---

In RenameContentType._rename(), content_type.save() must include 'using=db' to save to the correct database; the database alias is obtained from schema_editor.connection.alias.
