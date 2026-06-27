---
type: Memory
title: mem_acdd2eff
description: 'model_to_dict() returns all fields instead of empty dict when fields=[] is passed, because the condition `if fields and f.name not in fields:` treats an empty list as falsy.'
resource: 'memeval://memory/mem_acdd2eff'
tags:
- bug
- django
timestamp: '2026-06-27T11:41:54.641898+00:00'
x_item_id: mem_acdd2eff
x_relevancy: 1.0
x_version: 1
x_session_id: 5bebe6d0-1c8a-4a4a-a575-8e58e48cc580
x_source: daydream
x_tokens: 43
---

model_to_dict() returns all fields instead of empty dict when fields=[] is passed, because the condition `if fields and f.name not in fields:` treats an empty list as falsy.
