---
type: Fix
title: mem_c9ec7fb5
description: 'When extending a SymPy printer setting that was previously validated against a fixed dictionary table, wrap the table lookup in try-except KeyError and fall back to using the user''s value directly.'
resource: 'memeval://memory/mem_c9ec7fb5'
tags:
- sympy
- printing
- fix-pattern
timestamp: '2026-06-27T10:20:02.568766+00:00'
x_item_id: mem_c9ec7fb5
x_relevancy: 0.8
x_version: 1
x_session_id: 5dd4872d-4753-4bf4-80c2-d8ba836d7df3
x_source: daydream
x_tokens: 49
---

When extending a SymPy printer setting that was previously validated against a fixed dictionary table, wrap the table lookup in try-except KeyError and fall back to using the user's value directly.
