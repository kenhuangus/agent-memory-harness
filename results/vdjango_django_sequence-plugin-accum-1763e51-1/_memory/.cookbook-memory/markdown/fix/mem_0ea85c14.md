---
type: Fix
title: mem_0ea85c14
description: 'When validating ForeignKey values in Django, use `_base_manager` instead of `_default_manager` to avoid rejecting objects excluded by a custom default manager''s filters.'
resource: 'memeval://memory/mem_0ea85c14'
tags:
- ForeignKey
- validation
- base_manager
timestamp: '2026-06-27T06:03:33.260097+00:00'
x_item_id: mem_0ea85c14
x_relevancy: 1.0
x_version: 1
x_session_id: bf0711ec-c72a-470b-862f-e1586fbbe89e
x_source: daydream
x_tokens: 42
---

When validating ForeignKey values in Django, use `_base_manager` instead of `_default_manager` to avoid rejecting objects excluded by a custom default manager's filters.
