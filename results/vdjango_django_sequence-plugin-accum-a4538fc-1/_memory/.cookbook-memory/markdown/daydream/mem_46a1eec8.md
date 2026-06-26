---
type: Memory
title: mem_46a1eec8
description: 'Django''s migration serializer incorrectly serializes inner classes used as model fields, producing paths like ''test1.models.Inner'' instead of ''test1.models.Outer.Inner''.'
resource: 'memeval://memory/mem_46a1eec8'
tags:
- django-internals
- migrations-serializer
timestamp: '2026-06-26T16:44:14.024244+00:00'
x_item_id: mem_46a1eec8
x_relevancy: 1.0
x_version: 1
x_session_id: 96177036-0dfe-4c07-9cce-e5e3958dc6ee
x_source: daydream
x_tokens: 42
---

Django's migration serializer incorrectly serializes inner classes used as model fields, producing paths like 'test1.models.Inner' instead of 'test1.models.Outer.Inner'.
