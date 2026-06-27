---
type: Memory
title: mem_e256ad63
description: 'Vector.__sub__ at line 387 delegates to `self.__add__(other * -1)`, and Vector.__rsub__ at line 342 returns `(-1 * self) + other`; both benefit from fixes to `__add__`.'
resource: 'memeval://memory/mem_e256ad63'
tags:
- vector
- operator-convention
timestamp: '2026-06-26T17:32:44.613228+00:00'
x_item_id: mem_e256ad63
x_relevancy: 0.8
x_version: 1
x_session_id: 7ca2fb6d-8266-4fbe-a12e-3936bc73ed06
x_source: daydream
x_tokens: 42
---

Vector.__sub__ at line 387 delegates to `self.__add__(other * -1)`, and Vector.__rsub__ at line 342 returns `(-1 * self) + other`; both benefit from fixes to `__add__`.
