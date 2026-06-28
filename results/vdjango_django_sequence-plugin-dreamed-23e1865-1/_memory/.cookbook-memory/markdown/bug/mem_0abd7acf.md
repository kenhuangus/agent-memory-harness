---
type: Bug
title: mem_0abd7acf
description: 'In Django''s database cache backend, concurrent cache operations can cause _cull to find zero rows after counting — always fetch-and-test rather than fetch-and-subscript.'
resource: 'memeval://memory/mem_0abd7acf'
tags:
- django
- cache
- concurrency
timestamp: '2026-06-27T09:08:32.008354+00:00'
x_item_id: mem_0abd7acf
x_relevancy: 0.9
x_version: 1
x_session_id: 35b9680d-4c53-4eb9-9b01-3c39af9387c2
x_source: daydream
x_tokens: 42
---

In Django's database cache backend, concurrent cache operations can cause _cull to find zero rows after counting — always fetch-and-test rather than fetch-and-subscript.
