---
type: Fix
title: mem_f2ea370c
description: 'When resolving migration relations in resolve_relation(), splitting ''app_label.ModelName'' and lowercasing the whole tuple breaks mixed-case app labels.'
resource: 'memeval://memory/mem_f2ea370c'
tags:
- django
- migrations
- relation-resolution
timestamp: '2026-06-27T09:14:55.250235+00:00'
x_item_id: mem_f2ea370c
x_relevancy: 0.9
x_version: 1
x_session_id: 31d2ccd7-1ff7-4a62-88c7-77d438748ade
x_source: daydream
x_tokens: 48
---

When resolving migration relations in resolve_relation(), splitting 'app_label.ModelName' and lowercasing the whole tuple breaks mixed-case app labels. Only the model name should be lowercased.
