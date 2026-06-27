---
type: Memory
title: mem_f34997fb
description: 'The `as_set()` method in `sympy.logic.boolalg.Boolean.as_set()` now catches `NotImplementedError` from `_eval_as_set()` and returns a `ConditionSet` for `Equality` instances instead of re-raising.'
resource: 'memeval://memory/mem_f34997fb'
tags:
- sympy-core
- bugfix
timestamp: '2026-06-26T09:12:52.839826+00:00'
x_item_id: mem_f34997fb
x_relevancy: 1.0
x_version: 1
x_session_id: 635469dd-3996-48b1-ac63-132c8f5ef708
x_source: daydream
x_tokens: 49
---

The `as_set()` method in `sympy.logic.boolalg.Boolean.as_set()` now catches `NotImplementedError` from `_eval_as_set()` and returns a `ConditionSet` for `Equality` instances instead of re-raising.
