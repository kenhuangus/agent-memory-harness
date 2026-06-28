---
type: Fix
title: mem_5e86eda8
description: When symbols() with cls= and a tuple/list input recurses per element, the recursive call omits cls, defaulting to Symbol.
resource: 'memeval://memory/mem_5e86eda8'
tags:
- sympy
- symbols
- recursion
- keyword-argument
timestamp: '2026-06-27T20:57:48.720387+00:00'
x_item_id: mem_5e86eda8
x_relevancy: 0.95
x_version: 1
x_session_id: b65cef61-87be-4467-9b67-67ff959b577e
x_source: daydream
x_tokens: 41
---

When symbols() with cls= and a tuple/list input recurses per element, the recursive call omits cls, defaulting to Symbol. Fix: forward cls=cls in the recursive branch.
