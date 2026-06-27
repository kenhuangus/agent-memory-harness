---
type: Memory
title: mem_d670d7aa
description: 'The fix for DecimalField.to_python() is to change `except decimal.InvalidOperation:` to `except (decimal.InvalidOperation, TypeError):` on line 1504 of django/db/models/fields/__init__.py.'
resource: 'memeval://memory/mem_d670d7aa'
tags:
- django
- model-field
- fix
timestamp: '2026-06-27T06:31:11.433201+00:00'
x_item_id: mem_d670d7aa
x_relevancy: 0.9
x_version: 1
x_session_id: f8770cc6-7aaf-4b81-99f0-7819bc5dacc3
x_source: daydream
x_tokens: 47
---

The fix for DecimalField.to_python() is to change `except decimal.InvalidOperation:` to `except (decimal.InvalidOperation, TypeError):` on line 1504 of django/db/models/fields/__init__.py.
