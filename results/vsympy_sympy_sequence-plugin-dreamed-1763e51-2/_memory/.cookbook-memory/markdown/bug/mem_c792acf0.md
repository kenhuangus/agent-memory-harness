---
type: Bug
title: mem_c792acf0
description: When a DMP has unstripped leading zeros, methods like terms_gcd() and primitive() can crash with IndexError because they encounter an empty coefficient dictionary.
resource: 'memeval://memory/mem_c792acf0'
tags:
- bug-pattern
- polynomials
timestamp: '2026-06-27T12:55:34.167757+00:00'
x_item_id: mem_c792acf0
x_relevancy: 0.85
x_version: 1
x_session_id: ca740a4e-9f78-4f86-ba5a-603ab5fd2d33
x_source: daydream
x_tokens: 40
---

When a DMP has unstripped leading zeros, methods like terms_gcd() and primitive() can crash with IndexError because they encounter an empty coefficient dictionary.
