---
type: Fix
title: mem_af742a7a
description: When Add._eval_is_zero finds real parts cancel (b.is_zero) but pure imaginary terms exist (im and not im_or_z), return None not False because imaginary parts may also cancel.
resource: 'memeval://memory/mem_af742a7a'
tags:
- sympy
- core
- add
timestamp: '2026-06-27T23:51:59.310824+00:00'
x_item_id: mem_af742a7a
x_relevancy: 0.95
x_version: 1
x_session_id: f329a321-2d22-4fde-afa8-ca00e7d942a1
x_source: daydream
x_tokens: 43
---

When Add._eval_is_zero finds real parts cancel (b.is_zero) but pure imaginary terms exist (im and not im_or_z), return None not False because imaginary parts may also cancel.
