---
type: Memory
title: mem_c233a2b0
description: '`ModelFieldSerializer.serialize()` now passes `self.value.__class__` as the klass argument to `serialize_deconstructed`, enabling proper inner-class path resolution for all model field subclasses.'
resource: 'memeval://memory/mem_c233a2b0'
tags:
- django
- migrations
- serializer
- fix
timestamp: '2026-06-27T12:35:50.905172+00:00'
x_item_id: mem_c233a2b0
x_relevancy: 0.85
x_version: 1
x_session_id: ab7aa01b-0211-4dfb-afc8-7ba674a00891
x_source: daydream
x_tokens: 49
---

`ModelFieldSerializer.serialize()` now passes `self.value.__class__` as the klass argument to `serialize_deconstructed`, enabling proper inner-class path resolution for all model field subclasses.
