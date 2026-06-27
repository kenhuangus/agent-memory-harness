---
type: Fix
title: mem_88fc5f78
description: When filtering monomials by total degree (e.g., itermonomials min_degrees), use sum(powers.values()) not max(powers.values()) because max only checks the largest per-variable exponent, not the sum.
resource: 'memeval://memory/mem_88fc5f78'
tags:
- sympy
- polys
- monomials
timestamp: '2026-06-26T23:12:08.740318+00:00'
x_item_id: mem_88fc5f78
x_relevancy: 0.9
x_version: 1
x_session_id: 62664f3f-dfe7-4d09-a6d1-7d685e000881
x_source: daydream
x_tokens: 49
---

When filtering monomials by total degree (e.g., itermonomials min_degrees), use sum(powers.values()) not max(powers.values()) because max only checks the largest per-variable exponent, not the sum.
