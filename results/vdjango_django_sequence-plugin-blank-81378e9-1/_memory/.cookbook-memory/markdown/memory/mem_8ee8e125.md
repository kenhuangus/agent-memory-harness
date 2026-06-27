---
type: Memory
title: mem_8ee8e125
description: 'The bug fix changed the env-merging condition in BaseDatabaseClient.runshell() from ''if env:'' (truthy check) to ''if env is not None:'' so that an empty dict {} correctly triggers os.environ merging.'
resource: 'memeval://memory/mem_8ee8e125'
tags:
- django
- bug fix
- patch
timestamp: '2026-06-27T07:32:26.943768+00:00'
x_item_id: mem_8ee8e125
x_relevancy: 0.9
x_version: 1
x_session_id: 6ff8e15f-ffb9-4c4a-b069-937a4cbe687c
x_source: daydream
x_tokens: 49
---

The bug fix changed the env-merging condition in BaseDatabaseClient.runshell() from 'if env:' (truthy check) to 'if env is not None:' so that an empty dict {} correctly triggers os.environ merging.
