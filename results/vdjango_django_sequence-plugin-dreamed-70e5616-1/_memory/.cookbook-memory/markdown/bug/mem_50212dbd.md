---
type: Bug
title: mem_50212dbd
description: 'When the ''add'' template filter in Django encounters an exception it returns an empty string, so unpickled lazy proxies that lack __add__/__radd__ silently appear as empty output.'
resource: 'memeval://memory/mem_50212dbd'
tags:
- django
- template-filters
- exception-swallowing
timestamp: '2026-06-27T21:41:37.767176+00:00'
x_item_id: mem_50212dbd
x_relevancy: 0.8
x_version: 1
x_session_id: 85b07ed4-9c2f-4dcf-a57d-c99144b513ec
x_source: daydream
x_tokens: 44
---

When the 'add' template filter in Django encounters an exception it returns an empty string, so unpickled lazy proxies that lack __add__/__radd__ silently appear as empty output.
