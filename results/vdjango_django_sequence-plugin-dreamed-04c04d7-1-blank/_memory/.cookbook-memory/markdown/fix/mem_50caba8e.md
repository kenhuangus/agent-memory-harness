---
type: Fix
title: mem_50caba8e
description: 'When overriding `to_python()` in a Django ChoiceField subclass that catches exceptions, pass `params={''value'': value}` to `ValidationError` so the error message can include the rejected value.'
resource: 'memeval://memory/mem_50caba8e'
tags:
- django
- forms
- validation
- error-messages
timestamp: '2026-06-27T18:08:42.277415+00:00'
x_item_id: mem_50caba8e
x_relevancy: 0.95
x_version: 1
x_session_id: e5f8463c-fda0-4e55-a5a2-376a5719e365
x_source: daydream
x_tokens: 48
---

When overriding `to_python()` in a Django ChoiceField subclass that catches exceptions, pass `params={'value': value}` to `ValidationError` so the error message can include the rejected value.
