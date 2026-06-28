---
type: Fix
title: mem_42ec21ca
description: 'When making Django model enums (Choices subclasses) usable in templates, set do_not_call_in_templates as a @property on the base Choices class to prevent auto-calling by the template engine.'
resource: 'memeval://memory/mem_42ec21ca'
tags:
- django
- template
- enum
timestamp: '2026-06-27T16:42:18.687052+00:00'
x_item_id: mem_42ec21ca
x_relevancy: 0.8
x_version: 1
x_session_id: bc6ea656-9a9e-4111-98c8-69e9c7554bfc
x_source: daydream
x_tokens: 47
---

When making Django model enums (Choices subclasses) usable in templates, set do_not_call_in_templates as a @property on the base Choices class to prevent auto-calling by the template engine.
