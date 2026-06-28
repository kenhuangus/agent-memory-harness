---
type: Invariant
title: mem_f8ffec70
description: 'When a pk field has a callable default (e.g. uuid.uuid4), `self._meta.pk.default is not NOT_PROVIDED` is True; guard the force-insert logic with `raw=False` so loaddata can still update existing rows.'
resource: 'memeval://memory/mem_f8ffec70'
tags:
- django-serializers
- fixtures
- loaddata
timestamp: '2026-06-27T16:23:00.537355+00:00'
x_item_id: mem_f8ffec70
x_relevancy: 0.75
x_version: 1
x_session_id: 761b103d-ee9b-4f56-a065-219acac11cf3
x_source: daydream
x_tokens: 50
---

When a pk field has a callable default (e.g. uuid.uuid4), `self._meta.pk.default is not NOT_PROVIDED` is True; guard the force-insert logic with `raw=False` so loaddata can still update existing rows.
