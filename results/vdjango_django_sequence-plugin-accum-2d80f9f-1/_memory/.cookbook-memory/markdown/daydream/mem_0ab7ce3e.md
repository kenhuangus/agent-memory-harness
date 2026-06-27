---
type: Memory
title: mem_0ab7ce3e
description: MultiWidget.get_context() previously passed final_attrs by reference to subwidgets when there was no id, allowing mutation.
resource: 'memeval://memory/mem_0ab7ce3e'
tags:
- fix
- widgets
timestamp: '2026-06-26T05:42:09.179434+00:00'
x_item_id: mem_0ab7ce3e
x_relevancy: 0.85
x_version: 1
x_session_id: e11a014f-a709-4815-8fec-ced0a297a48d
x_source: daydream
x_tokens: 47
---

MultiWidget.get_context() previously passed final_attrs by reference to subwidgets when there was no id, allowing mutation. The fix copies the dict via final_attrs.copy() in the else branch.
