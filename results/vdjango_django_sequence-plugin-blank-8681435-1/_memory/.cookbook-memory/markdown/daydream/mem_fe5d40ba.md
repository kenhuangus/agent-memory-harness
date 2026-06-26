---
type: daydream
title: mem_fe5d40ba
description: 'Fixed in django/db/models/fields/related.py: ForeignObject.deconstruct() now splits the model reference on ''.'' and only lowercases the mode…'
resource: 'memeval://memory/mem_fe5d40ba'
tags:
- django
- code-change
- related.py
timestamp: '2026-06-25T22:08:55.961504+00:00'
x_item_id: mem_fe5d40ba
x_relevancy: 0.9
x_version: 1
x_session_id: bbe46570-82df-486b-950b-605d7b1b5431
x_source: daydream
x_tokens: 43
x_metadata_json: '{"extracted_from": "bbe46570-82df-486b-950b-605d7b1b5431"}'
---

Fixed in django/db/models/fields/related.py: ForeignObject.deconstruct() now splits the model reference on '.' and only lowercases the model name portion, not the app_label.
