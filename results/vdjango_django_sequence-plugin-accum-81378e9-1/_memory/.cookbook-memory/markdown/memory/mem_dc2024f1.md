---
type: Memory
title: mem_dc2024f1
description: 'The two username validators (`ASCIIUsernameValidator` and `UnicodeUsernameValidator`) shared the same regex pattern and both needed the same `^`→`\A`, `$`→`\Z` change.'
resource: 'memeval://memory/mem_dc2024f1'
tags:
- django
- pattern
- maintenance
timestamp: '2026-06-27T04:40:50.402757+00:00'
x_item_id: mem_dc2024f1
x_relevancy: 0.8
x_version: 1
x_session_id: 25de2a57-457c-4f19-8ddc-88f47f1a8ed4
x_source: daydream
x_tokens: 41
---

The two username validators (`ASCIIUsernameValidator` and `UnicodeUsernameValidator`) shared the same regex pattern and both needed the same `^`→`\A`, `$`→`\Z` change.
