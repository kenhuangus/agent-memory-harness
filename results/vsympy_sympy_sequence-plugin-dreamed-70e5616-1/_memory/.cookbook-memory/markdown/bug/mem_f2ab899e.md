---
type: Bug
title: mem_f2ab899e
description: When a DMP has an unstripped leading zero list element (e.g.
resource: 'memeval://memory/mem_f2ab899e'
timestamp: '2026-06-28T03:01:07.768812+00:00'
x_item_id: mem_f2ab899e
x_relevancy: 1.0
x_version: 1
x_session_id: 35817176-5162-42b9-a909-ab8c61376314
x_source: daydream
x_tokens: 43
---

When a DMP has an unstripped leading zero list element (e.g. DMP([EX(0)]) instead of DMP([])), is_zero returns False while as_expr() returns 0, causing inconsistent behavior.
