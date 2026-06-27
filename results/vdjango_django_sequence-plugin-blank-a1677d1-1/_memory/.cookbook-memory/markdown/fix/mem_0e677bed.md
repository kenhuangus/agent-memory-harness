---
type: Fix
title: mem_0e677bed
description: When constructing SQL aggregate templates that concatenate DISTINCT with an expression via string interpolation, include a trailing space after DISTINCT to avoid adjacent keywords merging.
resource: 'memeval://memory/mem_0e677bed'
tags:
- Django
- ORM
- SQL generation
timestamp: '2026-06-26T22:20:27.801771+00:00'
x_item_id: mem_0e677bed
x_relevancy: 0.9
x_version: 1
x_session_id: 8c1e2256-dd41-46ba-bb78-314d787814b3
x_source: daydream
x_tokens: 47
---

When constructing SQL aggregate templates that concatenate DISTINCT with an expression via string interpolation, include a trailing space after DISTINCT to avoid adjacent keywords merging.
