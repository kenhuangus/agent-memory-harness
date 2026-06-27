---
type: Fix
title: mem_0b02a66f
description: 'When implementing sqrtdenest''s `_sqrt_match` or any code calling `split_surds` on a sum, ensure all addend squares are positive rational before calling to avoid IndexError from empty surd list.'
resource: 'memeval://memory/mem_0b02a66f'
tags:
- denesting
- guard-condition
timestamp: '2026-06-27T00:22:26.792340+00:00'
x_item_id: mem_0b02a66f
x_relevancy: 0.9
x_version: 1
x_session_id: e50e9a69-aebd-4b4c-ab92-933b026afc03
x_source: daydream
x_tokens: 48
---

When implementing sqrtdenest's `_sqrt_match` or any code calling `split_surds` on a sum, ensure all addend squares are positive rational before calling to avoid IndexError from empty surd list.
