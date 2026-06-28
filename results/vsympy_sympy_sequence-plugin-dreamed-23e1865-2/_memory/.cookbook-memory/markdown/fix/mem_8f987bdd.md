---
type: Fix
title: mem_8f987bdd
description: When implementing an iterator that yields mutable objects (dict, list, set), yield a copy (e.g., .copy()) to prevent aliasing bugs when results are collected (e.g., list(yielder)).
resource: 'memeval://memory/mem_8f987bdd'
tags:
- generator
- bug prevention
timestamp: '2026-06-27T00:39:58.372937+00:00'
x_item_id: mem_8f987bdd
x_relevancy: 0.9
x_version: 1
x_session_id: ff13efd6-5d2f-4470-8982-5f65ab1f2d87
x_source: daydream
x_tokens: 45
---

When implementing an iterator that yields mutable objects (dict, list, set), yield a copy (e.g., .copy()) to prevent aliasing bugs when results are collected (e.g., list(yielder)).
