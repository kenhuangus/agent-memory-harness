---
type: Memory
title: mem_140ffba9
description: 'Changing a user''s email after a password reset token is generated invalidates the token because the email address is part of the token hash computed by _make_hash_value().'
resource: 'memeval://memory/mem_140ffba9'
tags:
- django
- security
- auth
timestamp: '2026-06-27T07:08:25.459121+00:00'
x_item_id: mem_140ffba9
x_relevancy: 0.9
x_version: 1
x_session_id: 77080b24-7ed9-4a38-90f2-faacf4444d6d
x_source: daydream
x_tokens: 42
---

Changing a user's email after a password reset token is generated invalidates the token because the email address is part of the token hash computed by _make_hash_value().
