---
type: Fix
title: mem_fefb4293
description: When clear_denoms() (or any operation that multiplies by a scalar) can produce a zero polynomial, always strip leading zeros from the DMP representation afterward with dmp_strip/dup_strip.
resource: 'memeval://memory/mem_fefb4293'
timestamp: '2026-06-28T03:01:07.768812+00:00'
x_item_id: mem_fefb4293
x_relevancy: 1.0
x_version: 1
x_session_id: 35817176-5162-42b9-a909-ab8c61376314
x_source: daydream
x_tokens: 47
---

When clear_denoms() (or any operation that multiplies by a scalar) can produce a zero polynomial, always strip leading zeros from the DMP representation afterward with dmp_strip/dup_strip.
