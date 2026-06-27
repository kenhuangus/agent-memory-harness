---
type: Memory
title: mem_f27a3d78
description: 'The bug: Django username validators (both ASCII and Unicode) accepted usernames ending with a newline because `$` in the original regex `r''^[\w.@+-]+$''` matched before a trailing `\n`.'
resource: 'memeval://memory/mem_f27a3d78'
tags:
- django
- bug
- validation
- security
timestamp: '2026-06-26T04:28:46.416309+00:00'
x_item_id: mem_f27a3d78
x_relevancy: 0.85
x_version: 1
x_session_id: cd5eea48-8ba6-46e5-92fc-e0fa9642ba88
x_source: daydream
x_tokens: 46
---

The bug: Django username validators (both ASCII and Unicode) accepted usernames ending with a newline because `$` in the original regex `r'^[\w.@+-]+$'` matched before a trailing `\n`.
