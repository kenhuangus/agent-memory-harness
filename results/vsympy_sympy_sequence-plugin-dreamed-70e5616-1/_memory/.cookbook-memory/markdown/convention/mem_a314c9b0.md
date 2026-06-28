---
type: Convention
title: mem_a314c9b0
description: 'When adding tests for `implemented_function`, delete each function''s `_imp_` attribute after test completion to prevent cross-test pollution from class-level caching.'
resource: 'memeval://memory/mem_a314c9b0'
tags:
- sympy
- testing
- convention
timestamp: '2026-06-27T23:26:27.333869+00:00'
x_item_id: mem_a314c9b0
x_relevancy: 1.0
x_version: 1
x_session_id: d1d55224-3908-4ddb-8c61-97b9c7d34b84
x_source: daydream
x_tokens: 41
---

When adding tests for `implemented_function`, delete each function's `_imp_` attribute after test completion to prevent cross-test pollution from class-level caching.
