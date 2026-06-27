---
type: Fix
title: mem_b84baa4e
description: 'When a Django template filter uses `+` for concatenation with lazy strings (gettext_lazy), it raises TypeError because __proxy__ lacks __radd__.'
resource: 'memeval://memory/mem_b84baa4e'
tags:
- Django
- templates
- lazy evaluation
- i18n
timestamp: '2026-06-27T18:00:42.815970+00:00'
x_item_id: mem_b84baa4e
x_relevancy: 0.95
x_version: 1
x_session_id: e7e91fc6-7708-45b2-acc8-46f696ac8150
x_source: daydream
x_tokens: 48
---

When a Django template filter uses `+` for concatenation with lazy strings (gettext_lazy), it raises TypeError because __proxy__ lacks __radd__. Fix: on TypeError, force str() on both operands.
