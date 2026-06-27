---
type: Fix
title: mem_a03badcb
description: 'When modifying a MathML presentation printer, `mi` elements must only contain text, not child elements like `msub`/`msup`/`msubsup` — return those as the root element directly.'
resource: 'memeval://memory/mem_a03badcb'
tags:
- sympy
- mathml
- printing
timestamp: '2026-06-27T07:20:11.772214+00:00'
x_item_id: mem_a03badcb
x_relevancy: 0.95
x_version: 1
x_session_id: 2037439c-3d37-4eb9-b4eb-19faea368ab5
x_source: daydream
x_tokens: 44
---

When modifying a MathML presentation printer, `mi` elements must only contain text, not child elements like `msub`/`msup`/`msubsup` — return those as the root element directly.
