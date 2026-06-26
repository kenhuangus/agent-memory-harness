---
type: Memory
title: mem_e41c11f3
description: 'ConditionSet._eval_subs had a bug on line 246 that used the substitution value (''new'') as the bound variable when the condition evaluated to S.true, creating a malformed ConditionSet.'
resource: 'memeval://memory/mem_e41c11f3'
tags:
- sympy
- sets
timestamp: '2026-06-26T06:16:14.421534+00:00'
x_item_id: mem_e41c11f3
x_relevancy: 1.0
x_version: 1
x_session_id: 12d56b56-5266-4587-a84d-ca2d3deed7c4
x_source: daydream
x_tokens: 45
---

ConditionSet._eval_subs had a bug on line 246 that used the substitution value ('new') as the bound variable when the condition evaluated to S.true, creating a malformed ConditionSet.
