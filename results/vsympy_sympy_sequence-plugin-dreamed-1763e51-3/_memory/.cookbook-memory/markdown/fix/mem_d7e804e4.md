---
type: Fix
title: mem_d7e804e4
description: 'When `_eval_as_set` calls `solve_univariate_inequality`, catch `NotImplementedError` and return a `ConditionSet` because that solver raises for unsolvable equations.'
resource: 'memeval://memory/mem_d7e804e4'
tags:
- sympy
- solver
- relational
- set
timestamp: '2026-06-27T17:45:30.076746+00:00'
x_item_id: mem_d7e804e4
x_relevancy: 0.9
x_version: 1
x_session_id: 3aeef791-e087-4d92-bfa6-c503b772c4c0
x_source: daydream
x_tokens: 41
---

When `_eval_as_set` calls `solve_univariate_inequality`, catch `NotImplementedError` and return a `ConditionSet` because that solver raises for unsolvable equations.
