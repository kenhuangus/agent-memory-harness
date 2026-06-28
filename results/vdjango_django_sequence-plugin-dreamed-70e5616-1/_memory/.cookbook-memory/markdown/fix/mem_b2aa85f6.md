---
type: Fix
title: mem_b2aa85f6
description: 'When a password reset token generator hashes user state, include the user''s email (via get_email_field_name()) in the hash so that changing the email invalidates outstanding tokens.'
resource: 'memeval://memory/mem_b2aa85f6'
tags:
- django
- security
- authentication
timestamp: '2026-06-27T10:07:54.144764+00:00'
x_item_id: mem_b2aa85f6
x_relevancy: 0.95
x_version: 1
x_session_id: 0843f5b0-8f8f-47ff-841f-b7736505f8eb
x_source: daydream
x_tokens: 45
---

When a password reset token generator hashes user state, include the user's email (via get_email_field_name()) in the hash so that changing the email invalidates outstanding tokens.
