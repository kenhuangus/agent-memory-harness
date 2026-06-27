---
type: Fix
title: mem_67f2e4c6
description: 'When implementing `__ne__` in a SymPy class, use `not self == other` instead of `not self.__eq__(other)` because `not NotImplemented` is `False` and would skip delegation.'
resource: 'memeval://memory/mem_67f2e4c6'
tags:
- comparison
- sympy
- inequality
timestamp: '2026-06-27T05:56:37.961326+00:00'
x_item_id: mem_67f2e4c6
x_relevancy: 0.95
x_version: 1
x_session_id: d075c943-615a-414c-9bf2-5353af51881b
x_source: daydream
x_tokens: 42
---

When implementing `__ne__` in a SymPy class, use `not self == other` instead of `not self.__eq__(other)` because `not NotImplemented` is `False` and would skip delegation.
