---
type: Memory
title: mem_9085af2f
description: Field.__deepcopy__ in django/forms/fields.py does not deep-copy the error_messages dictionary, causing all copies of a field to share the same dict and mutations to leak across instances.
resource: 'memeval://memory/mem_9085af2f'
tags:
- bug
- deepcopy
- django.forms.fields
- fix
timestamp: '2026-06-26T05:22:40.564429+00:00'
x_item_id: mem_9085af2f
x_relevancy: 1.0
x_version: 1
x_session_id: c32dbcc6-748c-4399-bcee-9b9713acc9f0
x_source: daydream
x_tokens: 46
---

Field.__deepcopy__ in django/forms/fields.py does not deep-copy the error_messages dictionary, causing all copies of a field to share the same dict and mutations to leak across instances.
