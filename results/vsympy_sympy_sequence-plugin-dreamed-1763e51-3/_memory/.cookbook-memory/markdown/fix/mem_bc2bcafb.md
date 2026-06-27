---
type: Fix
title: mem_bc2bcafb
description: 'When matching symbol name bases in SymPy''s split_super_sub or similar name-parsing utilities, a regex limited to [a-zA-Z] will omit Unicode letters (Greek, etc.) — use [^\\W\\d_]+ to match any letter.'
resource: 'memeval://memory/mem_bc2bcafb'
tags:
- sympy
- printing
- unicode
timestamp: '2026-06-27T16:18:07.402939+00:00'
x_item_id: mem_bc2bcafb
x_relevancy: 0.9
x_version: 1
x_session_id: 320d5a73-3da5-4814-baee-3b360d909c64
x_source: daydream
x_tokens: 50
---

When matching symbol name bases in SymPy's split_super_sub or similar name-parsing utilities, a regex limited to [a-zA-Z] will omit Unicode letters (Greek, etc.) — use [^\\W\\d_]+ to match any letter.
