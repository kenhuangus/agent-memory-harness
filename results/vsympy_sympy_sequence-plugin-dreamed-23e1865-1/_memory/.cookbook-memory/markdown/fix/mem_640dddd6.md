---
type: Fix
title: mem_640dddd6
description: 'When `itermonomials` filters by degree with integer max/min, the condition must use `sum(powers.values())` (total degree) not `max(powers.values())` (largest single exponent).'
resource: 'memeval://memory/mem_640dddd6'
tags:
- sympy
- monomials
- degree filtering
timestamp: '2026-06-27T20:53:40.737497+00:00'
x_item_id: mem_640dddd6
x_relevancy: 0.95
x_version: 1
x_session_id: e9c701c9-e994-46c1-8b6a-f50265fed922
x_source: daydream
x_tokens: 43
---

When `itermonomials` filters by degree with integer max/min, the condition must use `sum(powers.values())` (total degree) not `max(powers.values())` (largest single exponent).
