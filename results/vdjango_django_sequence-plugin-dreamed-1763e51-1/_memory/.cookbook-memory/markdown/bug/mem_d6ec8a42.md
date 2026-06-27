---
type: Bug
title: mem_d6ec8a42
description: 'When displaying JSONField values readonly in Django admin, `str()` on dicts produces Python repr, not valid JSON.'
resource: 'memeval://memory/mem_d6ec8a42'
tags:
- django
- admin
- jsonfield
- serialization
timestamp: '2026-06-27T09:00:31.888121+00:00'
x_item_id: mem_d6ec8a42
x_relevancy: 0.9
x_version: 1
x_session_id: 88af1815-8e18-4ba1-8927-54eb08101a88
x_source: daydream
x_tokens: 50
---

When displaying JSONField values readonly in Django admin, `str()` on dicts produces Python repr, not valid JSON. Use `field.get_prep_value()` to get proper JSON serialization via the field's encoder.
