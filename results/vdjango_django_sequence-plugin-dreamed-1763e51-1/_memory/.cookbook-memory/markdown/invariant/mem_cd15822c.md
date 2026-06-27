---
type: Invariant
title: mem_cd15822c
description: When using subprocess.run with an env argument, pass None (not an empty dict) to inherit the parent process environment; an empty dict clears the environment, causing silent bugs.
resource: 'memeval://memory/mem_cd15822c'
tags:
- subprocess
- environment
- Django
- bug_fix_pattern
timestamp: '2026-06-27T10:25:42.520373+00:00'
x_item_id: mem_cd15822c
x_relevancy: 1.0
x_version: 1
x_session_id: 9645d5ae-afab-4b2a-8282-f38eec3918b6
x_source: daydream
x_tokens: 44
---

When using subprocess.run with an env argument, pass None (not an empty dict) to inherit the parent process environment; an empty dict clears the environment, causing silent bugs.
