---
type: Convention
title: mem_bc977f84
description: 'When subclassing Django''s Field to create a read-only field, call `kwargs.setdefault(''disabled'', True)` in `__init__` — the disabled flag gates built-in tamper prevention in form processing.'
resource: 'memeval://memory/mem_bc977f84'
tags:
- Django
- forms
- best-practices
timestamp: '2026-06-27T17:56:34.381786+00:00'
x_item_id: mem_bc977f84
x_relevancy: 0.85
x_version: 1
x_session_id: 08cdf6f7-9860-497c-86f0-add50ad3c308
x_source: daydream
x_tokens: 47
---

When subclassing Django's Field to create a read-only field, call `kwargs.setdefault('disabled', True)` in `__init__` — the disabled flag gates built-in tamper prevention in form processing.
