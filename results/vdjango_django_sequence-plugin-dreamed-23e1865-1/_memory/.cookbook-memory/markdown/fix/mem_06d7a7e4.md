---
type: Fix
title: mem_06d7a7e4
description: 'When a Django model has a PK field with a default, the force-INSERT optimization must check `pk_explicit` and `raw` to avoid breaking explicit-PK saves and fixture reloading.'
resource: 'memeval://memory/mem_06d7a7e4'
tags:
- django-models
- pk-handling
- save-logic
- backward-compatibility
timestamp: '2026-06-27T16:23:00.537355+00:00'
x_item_id: mem_06d7a7e4
x_relevancy: 0.95
x_version: 1
x_session_id: 761b103d-ee9b-4f56-a065-219acac11cf3
x_source: daydream
x_tokens: 43
---

When a Django model has a PK field with a default, the force-INSERT optimization must check `pk_explicit` and `raw` to avoid breaking explicit-PK saves and fixture reloading.
