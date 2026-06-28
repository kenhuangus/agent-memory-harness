---
type: Bug
title: mem_f83bbca8
description: When a DMP has unstripped leading zeros, methods like terms_gcd() and primitive() can crash with IndexError because they encounter an empty coefficient dictionary.
resource: 'memeval://memory/mem_f83bbca8'
timestamp: '2026-06-28T03:01:07.768812+00:00'
x_item_id: mem_f83bbca8
x_relevancy: 1.0
x_version: 1
x_session_id: 35817176-5162-42b9-a909-ab8c61376314
x_source: daydream
x_tokens: 40
---

When a DMP has unstripped leading zeros, methods like terms_gcd() and primitive() can crash with IndexError because they encounter an empty coefficient dictionary.
