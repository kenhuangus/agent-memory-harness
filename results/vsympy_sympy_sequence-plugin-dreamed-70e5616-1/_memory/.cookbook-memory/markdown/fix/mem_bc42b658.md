---
type: Fix
title: mem_bc42b658
description: When editing densetools.py functions that multiply polynomials by scalars (dup_mul_ground/dmp_mul_ground), always strip the result afterward to avoid producing a DMP with unstripped leading zeros.
resource: 'memeval://memory/mem_bc42b658'
timestamp: '2026-06-28T03:01:07.768812+00:00'
x_item_id: mem_bc42b658
x_relevancy: 1.0
x_version: 1
x_session_id: 35817176-5162-42b9-a909-ab8c61376314
x_source: daydream
x_tokens: 49
---

When editing densetools.py functions that multiply polynomials by scalars (dup_mul_ground/dmp_mul_ground), always strip the result afterward to avoid producing a DMP with unstripped leading zeros.
