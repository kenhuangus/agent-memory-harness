---
type: Memory
title: mem_59db16bc
description: 'Fix location: `xarray/core/rolling.py`, `DataArrayRolling.__iter__()` method on line 270.'
resource: 'memeval://memory/mem_59db16bc'
tags:
- rolling.py
- __iter__
- center
timestamp: '2026-06-26T19:01:09.007079+00:00'
x_item_id: mem_59db16bc
x_relevancy: 0.98
x_version: 1
x_session_id: d1ad172a-5432-46e4-b5cf-495b1264bf65
x_source: daydream
x_tokens: 44
---

Fix location: `xarray/core/rolling.py`, `DataArrayRolling.__iter__()` method on line 270. The method needs to respect `self.center[0]` when computing window start/stop indices.
