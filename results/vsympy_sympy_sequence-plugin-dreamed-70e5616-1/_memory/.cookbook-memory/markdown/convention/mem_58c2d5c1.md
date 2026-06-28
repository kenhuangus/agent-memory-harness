---
type: Convention
title: mem_58c2d5c1
description: 'When writing `_has_matcher` in SymPy classes, return `lambda other: self == other` rather than `self.__eq__` to go through operator dispatch and respect `NotImplemented`.'
resource: 'memeval://memory/mem_58c2d5c1'
tags:
- sympy
- has
- pattern matching
timestamp: '2026-06-27T05:56:37.961326+00:00'
x_item_id: mem_58c2d5c1
x_relevancy: 0.85
x_version: 1
x_session_id: d075c943-615a-414c-9bf2-5353af51881b
x_source: daydream
x_tokens: 42
---

When writing `_has_matcher` in SymPy classes, return `lambda other: self == other` rather than `self.__eq__` to go through operator dispatch and respect `NotImplemented`.
