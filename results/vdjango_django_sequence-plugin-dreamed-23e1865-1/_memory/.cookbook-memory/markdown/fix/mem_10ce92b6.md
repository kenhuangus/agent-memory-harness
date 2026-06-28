---
type: Fix
title: mem_10ce92b6
description: When implementing template filters or operators that process lazy proxy objects (e.g., gettext_lazy), add __add__ and __radd__ to the proxy class to avoid TypeError from str + proxy concatenation.
resource: 'memeval://memory/mem_10ce92b6'
timestamp: '2026-06-27T06:35:32.481461+00:00'
x_item_id: mem_10ce92b6
x_relevancy: 0.9
x_version: 1
x_session_id: 9a2dd967-fd6c-41ec-81df-aefc4c713016
x_source: daydream
x_tokens: 49
---

When implementing template filters or operators that process lazy proxy objects (e.g., gettext_lazy), add __add__ and __radd__ to the proxy class to avoid TypeError from str + proxy concatenation.
