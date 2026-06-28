---
type: Fix
title: mem_21f85dae
description: 'When comparing version strings in Sphinx, use packaging.version.Version instead of Python string comparison to avoid incorrect lexicographic ordering (e.g., ''0.10'' < ''0.6'').'
resource: 'memeval://memory/mem_21f85dae'
tags:
- versioning
- bug fix
timestamp: '2026-06-28T00:41:49.436087+00:00'
x_item_id: mem_21f85dae
x_relevancy: 0.95
x_version: 1
x_session_id: bed48f02-e4a1-472a-96b6-d408104af2e2
x_source: daydream
x_tokens: 43
---

When comparing version strings in Sphinx, use packaging.version.Version instead of Python string comparison to avoid incorrect lexicographic ordering (e.g., '0.10' < '0.6').
