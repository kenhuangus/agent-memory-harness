---
type: Fix
title: mem_300e1afa
description: When dmp_ext_factor calls dmp_sqf_norm, use the original polynomial F, not the square-free part f, or the norm calculation drops distinct factors from the original.
resource: 'memeval://memory/mem_300e1afa'
tags:
- sympy
- polys
- factortools
timestamp: '2026-06-27T17:52:29.452369+00:00'
x_item_id: mem_300e1afa
x_relevancy: 0.95
x_version: 1
x_session_id: 71e461b3-136d-4130-ba7e-2b6feaeacadd
x_source: daydream
x_tokens: 41
---

When dmp_ext_factor calls dmp_sqf_norm, use the original polynomial F, not the square-free part f, or the norm calculation drops distinct factors from the original.
