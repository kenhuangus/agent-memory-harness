---
type: Bug
title: mem_df7dfd04
description: 'When adding non-enum attributes to a class inheriting from enum.Enum, use @property instead of a plain class attribute to avoid it becoming an enum member that breaks subclassing.'
resource: 'memeval://memory/mem_df7dfd04'
tags:
- python
- enum
- gotcha
timestamp: '2026-06-27T16:42:18.687052+00:00'
x_item_id: mem_df7dfd04
x_relevancy: 0.9
x_version: 1
x_session_id: bc6ea656-9a9e-4111-98c8-69e9c7554bfc
x_source: daydream
x_tokens: 44
---

When adding non-enum attributes to a class inheriting from enum.Enum, use @property instead of a plain class attribute to avoid it becoming an enum member that breaks subclassing.
