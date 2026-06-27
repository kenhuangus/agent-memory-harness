---
type: Bug
title: mem_a5aa6f69
description: 'When Django''s Collector has a fast-delete path (no dependencies, no signals), it must still clear the instance''s primary key to None after SQL deletion, matching the normal path''s behavior.'
resource: 'memeval://memory/mem_a5aa6f69'
tags:
- django
- ORM
- deletion
timestamp: '2026-06-27T08:21:55.282304+00:00'
x_item_id: mem_a5aa6f69
x_relevancy: 0.95
x_version: 1
x_session_id: fd6178d1-7e64-4b04-a4bf-dcdd6740f9a0
x_source: daydream
x_tokens: 47
---

When Django's Collector has a fast-delete path (no dependencies, no signals), it must still clear the instance's primary key to None after SQL deletion, matching the normal path's behavior.
