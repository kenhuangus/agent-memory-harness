---
type: Fix
title: mem_e386cb64
description: 'In Django''s Model.save(), the `force_insert = True` optimization for PK fields with defaults must check `self._state.pk_explicit` and `not raw` to preserve backward-compatible UPDATE behavior.'
resource: 'memeval://memory/mem_e386cb64'
tags:
- django
- save-logic
- regression
timestamp: '2026-06-27T08:53:38.525601+00:00'
x_item_id: mem_e386cb64
x_relevancy: 0.92
x_version: 1
x_session_id: 73daa1c5-323c-4867-85ca-ba735bcdf6b8
x_source: daydream
x_tokens: 48
---

In Django's Model.save(), the `force_insert = True` optimization for PK fields with defaults must check `self._state.pk_explicit` and `not raw` to preserve backward-compatible UPDATE behavior.
