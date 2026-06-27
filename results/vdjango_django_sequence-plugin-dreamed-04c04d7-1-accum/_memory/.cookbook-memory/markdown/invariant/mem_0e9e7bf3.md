---
type: Invariant
title: mem_0e9e7bf3
description: When model fields use TextChoices/IntegerChoices, the enum members inherit from str/int, so after to_python() extracts .value, the resulting value compares equal to the original enum member via ==.
resource: 'memeval://memory/mem_0e9e7bf3'
tags:
- django
- enum
timestamp: '2026-06-27T16:25:58.726164+00:00'
x_item_id: mem_0e9e7bf3
x_relevancy: 0.8
x_version: 1
x_session_id: 1fc9a190-7599-4408-bd4a-267b446c4a8b
x_source: daydream
x_tokens: 49
---

When model fields use TextChoices/IntegerChoices, the enum members inherit from str/int, so after to_python() extracts .value, the resulting value compares equal to the original enum member via ==.
