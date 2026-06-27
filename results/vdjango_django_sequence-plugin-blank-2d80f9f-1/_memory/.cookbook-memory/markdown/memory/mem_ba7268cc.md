---
type: Memory
title: mem_ba7268cc
description: 'The fix for the runshell os.environ bug is a one-line change in `django/db/backends/base/client.py` line 24: change `if env:` to `if env is not None:`.'
resource: 'memeval://memory/mem_ba7268cc'
tags:
- fix
- code
timestamp: '2026-06-27T14:21:19.955069+00:00'
x_item_id: mem_ba7268cc
x_relevancy: 0.9
x_version: 1
x_session_id: 72329e52-cac4-480c-877a-57683c336b3c
x_source: daydream
x_tokens: 37
---

The fix for the runshell os.environ bug is a one-line change in `django/db/backends/base/client.py` line 24: change `if env:` to `if env is not None:`.
