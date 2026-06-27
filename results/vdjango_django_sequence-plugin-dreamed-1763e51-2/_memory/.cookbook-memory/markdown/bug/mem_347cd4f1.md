---
type: Bug
title: mem_347cd4f1
description: When deep-copying a Django Form field, error_messages defaults to a shared dict unless __deepcopy__ explicitly copies it, causing form instances to share error message mutations.
resource: 'memeval://memory/mem_347cd4f1'
tags:
- django
- forms
timestamp: '2026-06-27T08:40:06.099703+00:00'
x_item_id: mem_347cd4f1
x_relevancy: 0.85
x_version: 1
x_session_id: 1d530fd2-720b-4b8e-9870-7c8d76a9df43
x_source: daydream
x_tokens: 44
---

When deep-copying a Django Form field, error_messages defaults to a shared dict unless __deepcopy__ explicitly copies it, causing form instances to share error message mutations.
