---
type: Fix
title: mem_668afe3e
description: 'When a `_sympify` call in a rich comparison method (`__eq__`, `__lt__`, etc.) raises `SympifyError`, return `NotImplemented` to allow Python to attempt the reflected comparison on the other object.'
resource: 'memeval://memory/mem_668afe3e'
tags:
- sympy
- comparison
- sympify
timestamp: '2026-06-27T05:56:37.961326+00:00'
x_item_id: mem_668afe3e
x_relevancy: 0.9
x_version: 1
x_session_id: d075c943-615a-414c-9bf2-5353af51881b
x_source: daydream
x_tokens: 49
---

When a `_sympify` call in a rich comparison method (`__eq__`, `__lt__`, etc.) raises `SympifyError`, return `NotImplemented` to allow Python to attempt the reflected comparison on the other object.
