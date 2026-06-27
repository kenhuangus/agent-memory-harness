---
type: Fix
title: mem_faf1ce4d
description: When printing Subs with LaTeX, always wrap the substituted expression in \left(...\right) to avoid ambiguous parenthesizing when the Subs is multiplied or contains an Add.
resource: 'memeval://memory/mem_faf1ce4d'
tags:
- sympy
- latex printing
- Subs
timestamp: '2026-06-26T22:59:53.076169+00:00'
x_item_id: mem_faf1ce4d
x_relevancy: 0.95
x_version: 1
x_session_id: 73fcd6c7-930d-43da-934b-ecc8d9a0d8e5
x_source: daydream
x_tokens: 42
---

When printing Subs with LaTeX, always wrap the substituted expression in \left(...\right) to avoid ambiguous parenthesizing when the Subs is multiplied or contains an Add.
