---
type: Workaround
title: mem_f5213bbe
description: When adding arithmetic overrides in SymPy quantum classes that would cause circular imports, check class name with other.__class__.__name__ instead of isinstance to avoid circular import errors.
resource: 'memeval://memory/mem_f5213bbe'
timestamp: '2026-06-27T07:59:56.556953+00:00'
x_item_id: mem_f5213bbe
x_relevancy: 0.7
x_version: 1
x_session_id: bef958ea-cd66-463c-8d34-0a1ae14c91fe
x_source: daydream
x_tokens: 48
---

When adding arithmetic overrides in SymPy quantum classes that would cause circular imports, check class name with other.__class__.__name__ instead of isinstance to avoid circular import errors.
