---
type: Fix
title: mem_6bd5c94d
description: 'When Django validation iterates over field names split by LOOKUP_SEP, handle ''pk'' as an alias by using model._meta.pk instead of get_field() to avoid nonexistent-field errors.'
resource: 'memeval://memory/mem_6bd5c94d'
tags:
- django
- model-validation
- ordering
timestamp: '2026-06-27T05:17:04.302609+00:00'
x_item_id: mem_6bd5c94d
x_relevancy: 0.9
x_version: 1
x_session_id: 9e85368a-b9aa-4f25-aaed-eefb2a7a00ad
x_source: daydream
x_tokens: 43
---

When Django validation iterates over field names split by LOOKUP_SEP, handle 'pk' as an alias by using model._meta.pk instead of get_field() to avoid nonexistent-field errors.
