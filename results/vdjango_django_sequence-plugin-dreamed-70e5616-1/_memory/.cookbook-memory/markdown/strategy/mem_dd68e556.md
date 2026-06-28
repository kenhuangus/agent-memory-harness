---
type: Strategy
title: mem_dd68e556
description: When a bug report describes shared state between instances after creation via copy.deepcopy, first check whether __deepcopy__ uses copy.copy without explicitly copying mutable container attributes.
resource: 'memeval://memory/mem_dd68e556'
tags:
- debugging
- python
- deepcopy
timestamp: '2026-06-28T00:50:32.722732+00:00'
x_item_id: mem_dd68e556
x_relevancy: 1.0
x_version: 1
x_session_id: 6f8cfbb2-ece1-4a37-8422-8bab11a1d6d9
x_source: daydream
x_tokens: 49
---

When a bug report describes shared state between instances after creation via copy.deepcopy, first check whether __deepcopy__ uses copy.copy without explicitly copying mutable container attributes.
