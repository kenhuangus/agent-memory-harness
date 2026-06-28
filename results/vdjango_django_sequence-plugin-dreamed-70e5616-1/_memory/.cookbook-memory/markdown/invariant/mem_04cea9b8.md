---
type: Invariant
title: mem_04cea9b8
description: In Django, Choices (TextChoices/IntegerChoices) inherit from both str/int and Enum; the str/int MRO is before Enum, so __str__ from the mixed-in type is used unless explicitly overridden.
resource: 'memeval://memory/mem_04cea9b8'
tags:
- python
- enum
- MRO
timestamp: '2026-06-27T05:26:31.151146+00:00'
x_item_id: mem_04cea9b8
x_relevancy: 0.85
x_version: 1
x_session_id: cf5cc173-729e-48ac-81f0-0c1b1e057191
x_source: daydream
x_tokens: 46
---

In Django, Choices (TextChoices/IntegerChoices) inherit from both str/int and Enum; the str/int MRO is before Enum, so __str__ from the mixed-in type is used unless explicitly overridden.
