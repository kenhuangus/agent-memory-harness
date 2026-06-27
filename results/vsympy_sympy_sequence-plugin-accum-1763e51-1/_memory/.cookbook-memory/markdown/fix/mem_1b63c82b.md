---
type: Fix
title: mem_1b63c82b
description: When split_surds encounters an expression with no power terms (surds empty), return (S.One, S.Zero, expr) early to prevent downstream IndexError.
resource: 'memeval://memory/mem_1b63c82b'
tags:
- functional-edge-case
- empty-input
timestamp: '2026-06-27T07:32:27.551496+00:00'
x_item_id: mem_1b63c82b
x_relevancy: 0.8
x_version: 1
x_session_id: 1f2bb08f-42a3-4c74-989c-32a08dc56936
x_source: daydream
x_tokens: 36
---

When split_surds encounters an expression with no power terms (surds empty), return (S.One, S.Zero, expr) early to prevent downstream IndexError.
