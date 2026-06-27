---
type: Fix
title: mem_8ff2ca43
description: When ForeignKey validation fails for records hidden by the default manager (e.g., archived), use _base_manager instead of _default_manager in validate() to allow valid records.
resource: 'memeval://memory/mem_8ff2ca43'
tags:
- django
- orm
- ForeignKey
- validation
timestamp: '2026-06-26T23:56:17.482005+00:00'
x_item_id: mem_8ff2ca43
x_relevancy: 0.9
x_version: 1
x_session_id: 71f30f26-c414-487e-864d-65e31a38121e
x_source: daydream
x_tokens: 44
---

When ForeignKey validation fails for records hidden by the default manager (e.g., archived), use _base_manager instead of _default_manager in validate() to allow valid records.
