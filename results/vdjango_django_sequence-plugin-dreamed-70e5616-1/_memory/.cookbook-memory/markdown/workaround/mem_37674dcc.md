---
type: Workaround
title: mem_37674dcc
description: 'When validating user input with Python regexes, be aware that `$` matches before a trailing newline by default; use `\A` and `\Z` instead of `^` and `$` to reject strings ending with a newline.'
resource: 'memeval://memory/mem_37674dcc'
timestamp: '2026-06-27T08:10:27.071436+00:00'
x_item_id: mem_37674dcc
x_relevancy: 0.9
x_version: 1
x_session_id: 530f9cb7-e75f-4d78-a978-4d986b0828ee
x_source: daydream
x_tokens: 48
---

When validating user input with Python regexes, be aware that `$` matches before a trailing newline by default; use `\A` and `\Z` instead of `^` and `$` to reject strings ending with a newline.
