---
type: Memory
title: mem_bc15de6e
description: 'Point.distance() in sympy/geometry/point.py uses zip() to pair coordinates, which silently truncates to the shorter point''s dimensions when Points have different dimensionalities.'
resource: 'memeval://memory/mem_bc15de6e'
tags:
- bug-behavior
timestamp: '2026-06-26T19:04:38.120491+00:00'
x_item_id: mem_bc15de6e
x_relevancy: 0.95
x_version: 1
x_session_id: 4f7422c9-5c41-42c7-aae7-dfc52cb8e7e3
x_source: daydream
x_tokens: 44
---

Point.distance() in sympy/geometry/point.py uses zip() to pair coordinates, which silently truncates to the shorter point's dimensions when Points have different dimensionalities.
