---
type: Fix
title: mem_b1572fc3
description: 'When passing a dynamically-generated list to a variadic function like _split_gcd(*surds), guard against empty list to avoid IndexError from a[0].'
resource: 'memeval://memory/mem_b1572fc3'
tags:
- defensive-call
- empty-input
timestamp: '2026-06-27T07:32:27.551496+00:00'
x_item_id: mem_b1572fc3
x_relevancy: 0.9
x_version: 1
x_session_id: 1f2bb08f-42a3-4c74-989c-32a08dc56936
x_source: daydream
x_tokens: 36
---

When passing a dynamically-generated list to a variadic function like _split_gcd(*surds), guard against empty list to avoid IndexError from a[0].
