---
type: Fix
title: mem_70af95df
description: 'When a field-like "part" in a LOOKUP_SEP chain cannot be resolved by get_field(), it may still be a valid transform or alias — first check for known aliases (pk) before raising an error.'
resource: 'memeval://memory/mem_70af95df'
tags:
- django
- model_checks
timestamp: '2026-06-27T16:10:49.501972+00:00'
x_item_id: mem_70af95df
x_relevancy: 1.0
x_version: 1
x_session_id: caf09beb-2f5a-4190-9a8f-682663f5f1c9
x_source: daydream
x_tokens: 46
---

When a field-like "part" in a LOOKUP_SEP chain cannot be resolved by get_field(), it may still be a valid transform or alias — first check for known aliases (pk) before raising an error.
