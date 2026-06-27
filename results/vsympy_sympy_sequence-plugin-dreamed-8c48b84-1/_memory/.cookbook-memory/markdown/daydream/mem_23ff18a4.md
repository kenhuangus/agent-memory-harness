---
type: Memory
title: mem_23ff18a4
description: '`Eq(n*cos(n) - 3*sin(n), 0).as_set()` now returns `ConditionSet(n, Eq(n*cos(n) - 3*sin(n), 0), Reals)` instead of raising `NotImplementedError`.'
resource: 'memeval://memory/mem_23ff18a4'
tags:
- regression-test
- issue-18211
timestamp: '2026-06-26T09:12:52.839826+00:00'
x_item_id: mem_23ff18a4
x_relevancy: 1.0
x_version: 1
x_session_id: 635469dd-3996-48b1-ac63-132c8f5ef708
x_source: daydream
x_tokens: 36
---

`Eq(n*cos(n) - 3*sin(n), 0).as_set()` now returns `ConditionSet(n, Eq(n*cos(n) - 3*sin(n), 0), Reals)` instead of raising `NotImplementedError`.
