---
type: Fix
title: mem_e5ebf9ba
description: 'When writing an AST visitor that extracts string literals, handle both `ast.Str` (Python < 3.8) and `ast.Constant` (Python >= 3.8) to avoid silent data loss.'
resource: 'memeval://memory/mem_e5ebf9ba'
tags:
- python
- AST
- compatibility
timestamp: '2026-06-28T02:50:43.583030+00:00'
x_item_id: mem_e5ebf9ba
x_relevancy: 1.0
x_version: 1
x_session_id: bc52f169-d5db-42a5-ac5e-b3a4843c7ecb
x_source: daydream
x_tokens: 39
---

When writing an AST visitor that extracts string literals, handle both `ast.Str` (Python < 3.8) and `ast.Constant` (Python >= 3.8) to avoid silent data loss.
