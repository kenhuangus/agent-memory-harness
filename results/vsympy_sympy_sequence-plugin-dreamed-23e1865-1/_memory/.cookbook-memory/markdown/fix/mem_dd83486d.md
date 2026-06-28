---
type: Fix
title: mem_dd83486d
description: When Add._eval_is_zero finds len(nz)==0 (all imaginary terms, no real non-zero ones), return None instead of False because imaginary parts can cancel to zero.
resource: 'memeval://memory/mem_dd83486d'
tags:
- is_zero
- Add
- imaginary
- cancellation
timestamp: '2026-06-27T20:24:38.448761+00:00'
x_item_id: mem_dd83486d
x_relevancy: 0.9
x_version: 1
x_session_id: 67bddae1-7784-4591-8e2e-ea4971801307
x_source: daydream
x_tokens: 39
---

When Add._eval_is_zero finds len(nz)==0 (all imaginary terms, no real non-zero ones), return None instead of False because imaginary parts can cancel to zero.
