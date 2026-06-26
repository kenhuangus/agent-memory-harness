---
type: daydream
title: mem_aafeb4c2
description: 'Rational(''0.5'', ''100'') returned 1/100100 instead of 1/200 because the __new__ method did `q *= p.q` which, when `q` is a string, performed …'
resource: 'memeval://memory/mem_aafeb4c2'
tags:
- sympy
- bug
- Rational
- numbers.py
timestamp: '2026-06-25T09:37:29.286710+00:00'
x_item_id: mem_aafeb4c2
x_relevancy: 0.95
x_version: 1
x_session_id: 11f86903-c72b-42bf-af2c-b25f8cee0775
x_source: daydream
x_tokens: 46
x_metadata_json: '{"extracted_from": "11f86903-c72b-42bf-af2c-b25f8cee0775"}'
---

Rational('0.5', '100') returned 1/100100 instead of 1/200 because the __new__ method did `q *= p.q` which, when `q` is a string, performed Python string repetition ('100' * 2 = '100100').
