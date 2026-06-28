---
type: Fix
title: mem_e87716fd
description: 'When a template uses adjacent placeholders like %(distinct)s%(expressions)s, ensure the distinct value includes its trailing space—the template won''t add it automatically.'
resource: 'memeval://memory/mem_e87716fd'
tags:
- django
- aggregate
- sql-generation
timestamp: '2026-06-27T23:47:01.457517+00:00'
x_item_id: mem_e87716fd
x_relevancy: 0.9
x_version: 1
x_session_id: 97842d0a-46e0-4502-8609-181da153327a
x_source: daydream
x_tokens: 42
---

When a template uses adjacent placeholders like %(distinct)s%(expressions)s, ensure the distinct value includes its trailing space—the template won't add it automatically.
