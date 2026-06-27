---
type: Fix
title: mem_e7caaa7a
description: When Django TextChoices/IntegerChoices misses a __str__, enum.Enum.__str__ returns qualified name instead of the .value.
resource: 'memeval://memory/mem_e7caaa7a'
tags:
- Django
- models
timestamp: '2026-06-27T16:14:26.676799+00:00'
x_item_id: mem_e7caaa7a
x_relevancy: 0.95
x_version: 1
x_session_id: b402dc65-6cf7-434a-bb27-604d9e7cc8c9
x_source: daydream
x_tokens: 47
---

When Django TextChoices/IntegerChoices misses a __str__, enum.Enum.__str__ returns qualified name instead of the .value. Fix: add __str__ to the Choices base class returning str(self.value).
