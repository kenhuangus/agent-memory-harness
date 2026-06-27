---
type: Memory
title: mem_f84b693f
description: '`ChoiceField` and `MultiValueField` override `__deepcopy__` but both call `super().__deepcopy__(memo)`, so the fix in the base `Field.__deepcopy__` automatically applies to them.'
resource: 'memeval://memory/mem_f84b693f'
tags:
- inheritance
- code-structure
- durability
timestamp: '2026-06-27T12:22:02.450185+00:00'
x_item_id: mem_f84b693f
x_relevancy: 0.92
x_version: 1
x_session_id: 65d17510-c11c-40cb-b2f2-c40ee0734efb
x_source: daydream
x_tokens: 44
---

`ChoiceField` and `MultiValueField` override `__deepcopy__` but both call `super().__deepcopy__(memo)`, so the fix in the base `Field.__deepcopy__` automatically applies to them.
