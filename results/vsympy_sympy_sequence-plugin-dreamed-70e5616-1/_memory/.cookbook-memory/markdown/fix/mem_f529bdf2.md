---
type: Fix
title: mem_f529bdf2
description: 'When `solve_univariate_inequality` raises `NotImplementedError` for an equation, catch it in `_eval_as_set` and return a `ConditionSet` instead of letting it propagate.'
resource: 'memeval://memory/mem_f529bdf2'
timestamp: '2026-06-28T02:25:09.762007+00:00'
x_item_id: mem_f529bdf2
x_relevancy: 0.95
x_version: 1
x_session_id: 6404e18f-d206-4bd7-bf64-f9da8443e48d
x_source: daydream
x_tokens: 42
---

When `solve_univariate_inequality` raises `NotImplementedError` for an equation, catch it in `_eval_as_set` and return a `ConditionSet` instead of letting it propagate.
