---
type: Fix
title: mem_ee1e86f6
description: 'When a utility function like _split_gcd unpacks a sequence argument, guard against empty input (e.g., return (S.One, [], [])) to avoid IndexError on `a[0]`.'
resource: 'memeval://memory/mem_ee1e86f6'
tags:
- sympy
- radsimp
timestamp: '2026-06-27T22:03:14.965141+00:00'
x_item_id: mem_ee1e86f6
x_relevancy: 0.8
x_version: 1
x_session_id: f4ad7174-9749-4e9e-9156-03bab3f7cc84
x_source: daydream
x_tokens: 39
---

When a utility function like _split_gcd unpacks a sequence argument, guard against empty input (e.g., return (S.One, [], [])) to avoid IndexError on `a[0]`.
