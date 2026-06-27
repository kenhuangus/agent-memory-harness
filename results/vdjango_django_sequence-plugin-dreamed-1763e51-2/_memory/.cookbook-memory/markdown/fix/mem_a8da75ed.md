---
type: Fix
title: mem_a8da75ed
description: 'When a Django Engine method creates a Context directly, it must pass autoescape=self.autoescape to respect the engine''s autoescape setting; otherwise Context defaults to autoescape=True.'
resource: 'memeval://memory/mem_a8da75ed'
tags:
- django
- templates
- autoescape
timestamp: '2026-06-27T04:52:47.265082+00:00'
x_item_id: mem_a8da75ed
x_relevancy: 0.95
x_version: 1
x_session_id: f3d967f1-7646-480b-8ec2-57e8d1f2dbe1
x_source: daydream
x_tokens: 46
---

When a Django Engine method creates a Context directly, it must pass autoescape=self.autoescape to respect the engine's autoescape setting; otherwise Context defaults to autoescape=True.
