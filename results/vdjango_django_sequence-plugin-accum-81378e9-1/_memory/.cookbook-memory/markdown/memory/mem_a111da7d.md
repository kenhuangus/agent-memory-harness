---
type: Memory
title: mem_a111da7d
description: 'DateFormat.Y() in django/utils/dateformat.py was changed from returning `self.data.year` to `return ''%04d'' % self.data.year` to properly zero-pad years < 1000 to four digits.'
resource: 'memeval://memory/mem_a111da7d'
tags:
- django
- dateformat
- bug-fix
timestamp: '2026-06-27T07:41:37.367062+00:00'
x_item_id: mem_a111da7d
x_relevancy: 0.9
x_version: 1
x_session_id: 96dbaeb6-5a2c-4058-9788-2c517e08099f
x_source: daydream
x_tokens: 43
---

DateFormat.Y() in django/utils/dateformat.py was changed from returning `self.data.year` to `return '%04d' % self.data.year` to properly zero-pad years < 1000 to four digits.
