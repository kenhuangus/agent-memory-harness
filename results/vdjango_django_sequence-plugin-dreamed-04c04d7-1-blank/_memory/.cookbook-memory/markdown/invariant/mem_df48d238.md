---
type: Invariant
title: mem_df48d238
description: 'When a Django form field has `disabled=True`, `_clean_fields()` retrieves initial values via `get_initial_for_field()`, ignoring submitted data — this is built-in tamper protection.'
resource: 'memeval://memory/mem_df48d238'
tags:
- Django
- forms
- security
timestamp: '2026-06-27T17:56:34.381786+00:00'
x_item_id: mem_df48d238
x_relevancy: 0.9
x_version: 1
x_session_id: 08cdf6f7-9860-497c-86f0-add50ad3c308
x_source: daydream
x_tokens: 45
---

When a Django form field has `disabled=True`, `_clean_fields()` retrieves initial values via `get_initial_for_field()`, ignoring submitted data — this is built-in tamper protection.
