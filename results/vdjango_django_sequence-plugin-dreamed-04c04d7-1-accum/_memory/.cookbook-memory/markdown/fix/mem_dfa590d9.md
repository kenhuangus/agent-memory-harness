---
type: Fix
title: mem_dfa590d9
description: When Django template auto-calls a callable class/object and it fails on required args, set do_not_call_in_templates = True on the object to prevent the call and allow normal attribute access.
resource: 'memeval://memory/mem_dfa590d9'
tags:
- django
- templates
- enums
timestamp: '2026-06-27T16:44:54.849436+00:00'
x_item_id: mem_dfa590d9
x_relevancy: 0.9
x_version: 1
x_session_id: e562a8d2-42f9-4ab0-b0a4-cc84d1b50d17
x_source: daydream
x_tokens: 47
---

When Django template auto-calls a callable class/object and it fails on required args, set do_not_call_in_templates = True on the object to prevent the call and allow normal attribute access.
