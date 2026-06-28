---
type: Fix
title: mem_d43cccba
description: 'When adding or fixing a Django model field''s to_python() method, catch TypeError and ValueError in addition to type-specific exceptions, and raise ValidationError instead of letting them propagate.'
resource: 'memeval://memory/mem_d43cccba'
tags:
- django
- model-field
- input-validation
timestamp: '2026-06-27T09:05:57.111979+00:00'
x_item_id: mem_d43cccba
x_relevancy: 1.0
x_version: 1
x_session_id: 33a67de0-c0e1-4429-9b99-3b940b44c4fd
x_source: daydream
x_tokens: 49
---

When adding or fixing a Django model field's to_python() method, catch TypeError and ValueError in addition to type-specific exceptions, and raise ValidationError instead of letting them propagate.
