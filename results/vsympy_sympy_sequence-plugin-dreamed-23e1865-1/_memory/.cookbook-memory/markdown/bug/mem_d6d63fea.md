---
type: Bug
title: mem_d6d63fea
description: When implementing quaternion-to-rotation-matrix conversion, sign errors in cross-product terms can cause reflection instead of rotation; validate against a standard reference.
resource: 'memeval://memory/mem_d6d63fea'
tags:
- quaternion
- rotation_matrix
- formula
timestamp: '2026-06-27T21:48:58.458516+00:00'
x_item_id: mem_d6d63fea
x_relevancy: 0.8
x_version: 1
x_session_id: b03445d0-3ab9-4ed1-b033-3c69e10d2715
x_source: daydream
x_tokens: 43
---

When implementing quaternion-to-rotation-matrix conversion, sign errors in cross-product terms can cause reflection instead of rotation; validate against a standard reference.
