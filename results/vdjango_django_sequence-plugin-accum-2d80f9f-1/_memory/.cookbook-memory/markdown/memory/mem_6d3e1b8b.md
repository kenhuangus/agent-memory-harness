---
type: Memory
title: mem_6d3e1b8b
description: 'Django form fields'' `__deepcopy__` method (django/forms/fields.py, Field class) does not deep-copy the `error_messages` dictionary, causing all deep copies of a field to share the same dictionary.'
resource: 'memeval://memory/mem_6d3e1b8b'
tags:
- bug
- form
- deepcopy
timestamp: '2026-06-27T12:27:44.221429+00:00'
x_item_id: mem_6d3e1b8b
x_relevancy: 0.9
x_version: 1
x_session_id: eb65630f-d28f-4a6d-ae5e-a7e12e8b619e
x_source: daydream
x_tokens: 49
---

Django form fields' `__deepcopy__` method (django/forms/fields.py, Field class) does not deep-copy the `error_messages` dictionary, causing all deep copies of a field to share the same dictionary.
