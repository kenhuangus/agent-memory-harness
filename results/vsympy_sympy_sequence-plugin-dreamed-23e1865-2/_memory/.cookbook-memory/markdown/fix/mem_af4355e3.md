---
type: Fix
title: mem_af4355e3
description: 'When _split_gcd receives an empty argument list (no surds found), it must handle this gracefully by returning (S.One, [], []) instead of indexing a[0] on an empty tuple.'
resource: 'memeval://memory/mem_af4355e3'
tags:
- radsimp
- _split_gcd
- surds
timestamp: '2026-06-27T19:08:08.452415+00:00'
x_item_id: mem_af4355e3
x_relevancy: 0.7
x_version: 1
x_session_id: 5d8c37b9-9021-4e1d-9203-b693faf3cbd6
x_source: daydream
x_tokens: 42
---

When _split_gcd receives an empty argument list (no surds found), it must handle this gracefully by returning (S.One, [], []) instead of indexing a[0] on an empty tuple.
