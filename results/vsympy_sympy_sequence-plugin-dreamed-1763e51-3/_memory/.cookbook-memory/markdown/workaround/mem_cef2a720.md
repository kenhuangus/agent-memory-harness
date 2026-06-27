---
type: Workaround
title: mem_cef2a720
description: 'When importing SymPy functions inside matrix expression `_entry` methods, use a local import (e.g., `from sympy import KroneckerDelta`) to avoid circular import issues at module load time.'
resource: 'memeval://memory/mem_cef2a720'
tags:
- sympy
- import
- workaround
timestamp: '2026-06-27T16:55:24.224962+00:00'
x_item_id: mem_cef2a720
x_relevancy: 0.7
x_version: 1
x_session_id: e540793a-b5ba-474b-8d67-3f707c337ac7
x_source: daydream
x_tokens: 47
---

When importing SymPy functions inside matrix expression `_entry` methods, use a local import (e.g., `from sympy import KroneckerDelta`) to avoid circular import issues at module load time.
