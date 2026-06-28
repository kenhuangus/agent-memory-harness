---
type: Fix
title: mem_1deb2ccd
description: 'When using \S in a regex for URL user:password authentication, it accepts forbidden characters like @ : / .'
resource: 'memeval://memory/mem_1deb2ccd'
tags:
- validation
- security
- url
timestamp: '2026-06-27T19:57:57.386528+00:00'
x_item_id: mem_1deb2ccd
x_relevancy: 0.95
x_version: 1
x_session_id: 43ce82d1-a862-4349-8a98-0ec2cfe48a35
x_source: daydream
x_tokens: 48
---

When using \S in a regex for URL user:password authentication, it accepts forbidden characters like @ : / . Replace \S with an explicit excluded character class like [^\s:@/] to enforce RFC 1738.
