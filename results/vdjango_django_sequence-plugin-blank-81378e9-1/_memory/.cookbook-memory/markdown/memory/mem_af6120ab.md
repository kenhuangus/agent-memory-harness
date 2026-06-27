---
type: Memory
title: mem_af6120ab
description: 'In django.utils.dateformat.DateFormat, the Y() method was changed to use ''%04d'' % self.data.year instead of returning the raw year, ensuring years < 1000 are zero-padded to 4 digits.'
resource: 'memeval://memory/mem_af6120ab'
tags:
- bug-fix
- date-formatting
timestamp: '2026-06-27T07:34:24.842425+00:00'
x_item_id: mem_af6120ab
x_relevancy: 0.95
x_version: 1
x_session_id: 7885e55f-6ba9-41a7-984f-87e4249c1ac5
x_source: daydream
x_tokens: 45
---

In django.utils.dateformat.DateFormat, the Y() method was changed to use '%04d' % self.data.year instead of returning the raw year, ensuring years < 1000 are zero-padded to 4 digits.
