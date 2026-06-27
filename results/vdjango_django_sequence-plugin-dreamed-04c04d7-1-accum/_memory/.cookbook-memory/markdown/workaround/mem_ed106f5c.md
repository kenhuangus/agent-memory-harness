---
type: Workaround
title: mem_ed106f5c
description: 'When verifying `__deepcopy__` correctness on objects with mutable attributes, deepcopy the object, mutate a mutable attribute on the copy, then assert the original''s attribute is unchanged.'
resource: 'memeval://memory/mem_ed106f5c'
tags:
- testing
- deepcopy
timestamp: '2026-06-27T16:15:08.458987+00:00'
x_item_id: mem_ed106f5c
x_relevancy: 0.8
x_version: 1
x_session_id: 3a8d855e-901e-4cb9-baa7-f4bd40c235cc
x_source: daydream
x_tokens: 47
---

When verifying `__deepcopy__` correctness on objects with mutable attributes, deepcopy the object, mutate a mutable attribute on the copy, then assert the original's attribute is unchanged.
