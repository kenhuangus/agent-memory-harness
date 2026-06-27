---
type: Workaround
title: mem_cd6b9533
description: When writing test cases for Django migration serializers, define mock classes at module level because the serialize() method requires importable paths to resolve class references.
resource: 'memeval://memory/mem_cd6b9533'
tags:
- django
- testing
timestamp: '2026-06-27T05:30:14.399428+00:00'
x_item_id: mem_cd6b9533
x_relevancy: 0.7
x_version: 1
x_session_id: 21319a15-117e-4bcd-9630-584fa897bfcb
x_source: daydream
x_tokens: 44
---

When writing test cases for Django migration serializers, define mock classes at module level because the serialize() method requires importable paths to resolve class references.
