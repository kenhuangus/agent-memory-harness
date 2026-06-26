---
type: Memory
title: mem_adfa50ec
description: 'In ModelChoiceField.to_python(), the queryset.get() result must be stored in a separate variable (obj) to avoid overwriting the original ''value'' parameter used for error message params.'
resource: 'memeval://memory/mem_adfa50ec'
tags:
- django
- forms
- coding-pattern
- python
timestamp: '2026-06-26T07:17:04.798543+00:00'
x_item_id: mem_adfa50ec
x_relevancy: 0.92
x_version: 1
x_session_id: d674f8aa-ee4c-429d-8f49-825c24bb5fb2
x_source: daydream
x_tokens: 46
---

In ModelChoiceField.to_python(), the queryset.get() result must be stored in a separate variable (obj) to avoid overwriting the original 'value' parameter used for error message params.
