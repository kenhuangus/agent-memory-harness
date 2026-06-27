---
type: Fix
title: mem_e9bcf451
description: When ForeignKey.validate() needs to check if a related object exists, it should use _base_manager instead of _default_manager so instances filtered by a custom default manager are still accepted.
resource: 'memeval://memory/mem_e9bcf451'
tags:
- django
- models
- ForeignKey
- validation
- managers
timestamp: '2026-06-27T17:05:44.768343+00:00'
x_item_id: mem_e9bcf451
x_relevancy: 0.95
x_version: 1
x_session_id: d1e6ca97-a23b-42eb-9fd1-1794d524d184
x_source: daydream
x_tokens: 48
---

When ForeignKey.validate() needs to check if a related object exists, it should use _base_manager instead of _default_manager so instances filtered by a custom default manager are still accepted.
