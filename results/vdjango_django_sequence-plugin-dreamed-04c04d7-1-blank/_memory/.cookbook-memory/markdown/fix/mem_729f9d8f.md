---
type: Fix
title: mem_729f9d8f
description: 'When a Django ModelForm''s clean() sets a field not in Meta.fields, the construct_instance function must bypass its `fields` parameter check for non-form fields to apply the cleaned_data value.'
resource: 'memeval://memory/mem_729f9d8f'
tags:
- Django
- ModelForm
- fields parameter
timestamp: '2026-06-27T15:46:47.987716+00:00'
x_item_id: mem_729f9d8f
x_relevancy: 0.9
x_version: 1
x_session_id: dc76e683-961d-414a-a6d4-6ff789138ca4
x_source: daydream
x_tokens: 48
---

When a Django ModelForm's clean() sets a field not in Meta.fields, the construct_instance function must bypass its `fields` parameter check for non-form fields to apply the cleaned_data value.
