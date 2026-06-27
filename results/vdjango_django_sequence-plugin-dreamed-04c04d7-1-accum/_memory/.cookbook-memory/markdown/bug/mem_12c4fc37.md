---
type: Bug
title: mem_12c4fc37
description: 'When a method returns an empty dict to signal "no custom env vars", the caller must distinguish empty dict from None before passing to subprocess.run — empty dict means empty environment, not inherit.'
resource: 'memeval://memory/mem_12c4fc37'
tags:
- django
- subprocess
- environment-variables
timestamp: '2026-06-27T18:03:43.446902+00:00'
x_item_id: mem_12c4fc37
x_relevancy: 0.95
x_version: 1
x_session_id: 6e5c18f4-7a8d-41c5-8502-3344a7fa2679
x_source: daydream
x_tokens: 50
---

When a method returns an empty dict to signal "no custom env vars", the caller must distinguish empty dict from None before passing to subprocess.run — empty dict means empty environment, not inherit.
