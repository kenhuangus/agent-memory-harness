---
type: Memory
title: mem_8705db3f
description: 'The fix for the trailing-newline username validator bug is to change the regex in both `ASCIIUsernameValidator` and `UnicodeUsernameValidator` from `r''^[\w.@+-]+$''` to `r''\A[\w.@+-]+\Z''`.'
resource: 'memeval://memory/mem_8705db3f'
tags:
- Django
- fix
timestamp: '2026-06-27T11:34:22.464692+00:00'
x_item_id: mem_8705db3f
x_relevancy: 0.95
x_version: 1
x_session_id: 4b5f771b-5aa4-4fb0-acbe-3729d097a786
x_source: daydream
x_tokens: 46
---

The fix for the trailing-newline username validator bug is to change the regex in both `ASCIIUsernameValidator` and `UnicodeUsernameValidator` from `r'^[\w.@+-]+$'` to `r'\A[\w.@+-]+\Z'`.
