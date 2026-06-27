---
type: Fix
title: mem_0869d4ed
description: 'When implementing or reviewing quaternion-to-rotation-matrix code, the element m12 (M[0][1]) uses 2*s*(c*d - b*a) not 2*s*(c*d + b*a).'
resource: 'memeval://memory/mem_0869d4ed'
tags:
- quaternion
- rotation
- sign
timestamp: '2026-06-27T06:28:15.877629+00:00'
x_item_id: mem_0869d4ed
x_relevancy: 0.9
x_version: 1
x_session_id: 1f6c67e3-ef46-45d2-9525-6f7270a4c8d4
x_source: daydream
x_tokens: 47
---

When implementing or reviewing quaternion-to-rotation-matrix code, the element m12 (M[0][1]) uses 2*s*(c*d - b*a) not 2*s*(c*d + b*a). The standard formula from Wikipedia must be consulted.
