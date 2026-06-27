---
type: Fix
title: mem_12e8e6b4
description: 'When overriding Django''s Field.__init__() in a custom form field, use `kwargs.setdefault(''disabled'', True)` so the base class handles all disabled-HTML-attribute logic and tamper prevention.'
resource: 'memeval://memory/mem_12e8e6b4'
tags:
- django
- forms
timestamp: '2026-06-27T06:33:02.882835+00:00'
x_item_id: mem_12e8e6b4
x_relevancy: 0.8
x_version: 1
x_session_id: 42632385-77c2-4f65-ba9d-dc248293f65b
x_source: daydream
x_tokens: 47
---

When overriding Django's Field.__init__() in a custom form field, use `kwargs.setdefault('disabled', True)` so the base class handles all disabled-HTML-attribute logic and tamper prevention.
