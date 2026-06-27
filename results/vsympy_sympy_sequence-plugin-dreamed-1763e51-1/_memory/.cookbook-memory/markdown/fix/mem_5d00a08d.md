---
type: Fix
title: mem_5d00a08d
description: 'When printing Indexed expressions, unpack `base, *index = expr.args` and return `f''{self._print(base)}["{", ".join(self._print(i) for i in index)}"]`.'
resource: 'memeval://memory/mem_5d00a08d'
tags:
- sympy
- printing
- Indexed
timestamp: '2026-06-27T08:45:16.568205+00:00'
x_item_id: mem_5d00a08d
x_relevancy: 1.0
x_version: 1
x_session_id: 5e142ebd-118e-4fcf-b347-85d85635def4
x_source: daydream
x_tokens: 37
---

When printing Indexed expressions, unpack `base, *index = expr.args` and return `f'{self._print(base)}["{", ".join(self._print(i) for i in index)}"]`.
