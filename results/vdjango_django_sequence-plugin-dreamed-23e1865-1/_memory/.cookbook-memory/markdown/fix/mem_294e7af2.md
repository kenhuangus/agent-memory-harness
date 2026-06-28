---
type: Fix
title: mem_294e7af2
description: 'When Django template engine methods create a Context, always pass autoescape=self.autoescape to honor the engine''s autoescape setting; otherwise the default True overrides the user configuration.'
resource: 'memeval://memory/mem_294e7af2'
tags:
- Django
- template
- autoescape
timestamp: '2026-06-27T08:12:26.702676+00:00'
x_item_id: mem_294e7af2
x_relevancy: 0.9
x_version: 1
x_session_id: 5751e6f1-d2df-450a-8d5f-30635cb8310a
x_source: daydream
x_tokens: 48
---

When Django template engine methods create a Context, always pass autoescape=self.autoescape to honor the engine's autoescape setting; otherwise the default True overrides the user configuration.
